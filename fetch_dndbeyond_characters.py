import os, re, csv, asyncio, contextlib
from discord.ext import commands
from discord import Intents
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()
# --- settings --------------------------------------------------------------
TOKEN      = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int("1279048805194530817")#1378673668279762954int(os.getenv("CHANNEL_ID"))
print(CHANNEL_ID)

# Works both for /characters/<id> and /profile/<name>/characters/<id>
URL_RE = re.compile(
    r"https?://"                       # http://  or  https://
    r"(?:www\.)?"                      # optional  www.
    r"dndbeyond\.com/"                 # dndbeyond.com/
    r"(?:profile/[^/]+/)?"             # optional  profile/<name>/
    r"characters/"                     # characters/
    r"\d+"                             # numeric character-ID
    r"(?:/[A-Za-z0-9_-]+)?"            # optional trailing slug like  dCXTkG
)

CSV_PATH = "characters.csv"
FAILED_CSV_PATH = "failedtoscrapecharacters.csv"
HEADLESS = True          # set to False while debugging scraping
CONCURRENCY = 5          # how many pages to fetch in parallel
scrapefail = []
# ---------------------------------------------------------------------------


async def scrape_character(pair, playwright):
    """Return dict or None on failure."""
    browser = await playwright.chromium.launch(headless=HEADLESS)
    page    = await browser.new_page()
    try:
        thread_name, url = pair
        print(url)
        await page.goto(url, timeout=90_000)
        # Wait for the header that always loads, even without logging in
        await page.wait_for_selector("h1", timeout=10_000)

        # --- The selectors below are current as of May 2025 -------------
        locator = page.locator('span.ddbc-xp-bar__label').first
        raw_text = await locator.inner_text()          # e.g. 'LVL 8'
        level = int(re.search(r'\d+', raw_text).group())
        print(level)
        locator    = page.locator('[class="ddbc-character-summary__classes"]').first
        class_text = await locator.inner_text()          # e.g. 'Fighter'
        #print(class_text)
        locator    = page.locator('[class="ddbc-character-summary__race"]').first
        species = await locator.inner_text()          # e.g. 'Human'
        #print(species)
        armorclass = await page.locator('[data-testid="armor-class-value"]').inner_text()
        #print(armorclass)
        maxhp = await page.locator('[data-testid="max-hp"]').inner_text()
        #print(maxhp)
        #subclass_text = await page.locator('[data-testid="class-summary-subclass"]').inner_text()
        #Ability Scores
        abilities = page.locator('.ddbc-ability-summary')      # list-like Locator
        ability_scores = {}
        for i in range(await abilities.count()):
            block   = abilities.nth(i)
            name    = (await block.locator('.ddbc-ability-summary__label')
                                .inner_text()).strip()          # "Strength"
            score   = int((await block.locator('.ddbc-ability-summary__secondary')
                                    .inner_text()).replace('\n', '').strip())    # 20
            ability_scores[name] = score

        #print(ability_scores)
        #Saving Throws
        saving_throws = {}                                # str → '+8', int → '+3', …

        rows = page.locator('.ddbc-saving-throws-summary__ability')
        for i in range(await rows.count()):
            row   = rows.nth(i)

            # 1. ability abbreviation: str / dex / con / …
            ability = (await row.locator(
                '.ddbc-saving-throws-summary__ability-name abbr'
            ).inner_text()).strip().lower()

            # 2. modifier: "+8", "-1", "+0", …
            mod_text = (await row.locator(
                '.ddbc-saving-throws-summary__ability-modifier'
            ).inner_text()).replace('\n', '').strip()          # already contains + / -

            # store or print
            saving_throws[ability] = mod_text
            #print(f"{ability} {mod_text}")
        
        #Subclass
        features_tab = page.get_by_role("radio", name="Features & Traits", exact=True)
        root = page

        if await features_tab.is_visible():
            await features_tab.click()
        else:
            frame = page.frame_locator('iframe[src*="characters"]').frame()
            features_tab = frame.get_by_role("radio", name="Features & Traits", exact=True)
            await features_tab.click()
            root = frame

        # --- 2. wait for the Class-features column to exist -----------------------
        await root.wait_for_selector(".ct-class-detail__features", timeout=15_000, state="visible")

        # Some sheets lazy-render inside that column only after first scroll
        await root.locator(".ct-class-detail__features").first.scroll_into_view_if_needed()
        await root.wait_for_timeout(500)          # give React a beat to mount snippets

        # --- 3. locate every class-feature snippet --------------------------------
        snippets = root.locator(".ct-class-detail__features .ct-feature-snippet--class")
        count = await snippets.count()
        #print("DEBUG: snippet blocks found →", count)

        if count == 0:
            #print("No snippets rendered; try a longer wait or scroll")
            return

        # --- 4. find any heading that ends with “Subclass” ------------------------
        SUBCLASS_RE = re.compile(r"\bsubclass\b", re.I)
        subclass_names = []

        for i in range(count):
            block = snippets.nth(i)
            heading_node = block.locator(".styles_heading__yD0Cm").first
            heading = (await heading_node.inner_text()).strip()

            # Remove level prefixes like "3:" or "4: "
            heading_core = heading.split(":", 1)[-1].strip()

            #print("DEBUG: heading →", heading_core)             # ← inspect what we see

            if SUBCLASS_RE.search(heading_core):
                choice = block.locator(".ct-feature-snippet__choice").first
                subclass = (await choice.inner_text()).strip()
                subclass_names.append(subclass)

        # --- 5. report ------------------------------------------------------------
        if subclass_names:
            print("Subclass found ➜", subclass_names)
        else:
            print("Subclass not present on this sheet")
        # ----------------------------------------------------------------
        return dict(url=url, name=thread_name, species=species, level=level, cls=class_text.strip(), subclass = subclass_names, abilityscores = ability_scores, savingthrows = saving_throws, ac = armorclass, hp = maxhp)
    except Exception as e:
        print(f"[warn] {url} – {e}")
        global scrapefail
        scrapefail.append(dict(url=url, name=thread_name))
        return None
    finally:
        await browser.close()


async def gather_discord_urls(bot) -> list[str]:
    """Collect every D&D Beyond URL in the target channel (flat or forum)."""
    channel = bot.get_channel(CHANNEL_ID)
    pairs = set()
    urls = set()
    print(channel)
    try:
        # Forum channels (GUILD_FORUM) consist of many threads (“posts”)
        if hasattr(channel, "threads"):
            print(channel)
            # First, pick up already-created threads
            for thread in channel.threads:
                try:
                    print(thread.name)
                    async for m in thread.history(limit=None, oldest_first=True):
                        #print(m)
                        match = URL_RE.search(m.content)
                        if match:
                            url = match.group()
                            pairs.add((thread.name, url))
                            break
                except Exception as e:
                    print("Exception in thread", e)

            # Then, fetch archived threads just in case
            '''async for thread in channel.archived_threads(limit=None):
                print(thread)
                async for m in thread.history(limit=None, oldest_first=True):
                    #print(m)
                    match = URL_RE.search(m.content)
                    if match:
                        url = match.group()
                        pairs.add((thread.name, url))
                        break'''

        # Plain text / announcement / news channels
        else:
            async for m in channel.history(limit=None, oldest_first=True):
                print(m)
                urls.update(URL_RE.findall(m.content))

        return sorted(pairs)
    except Exception as e:
        print("Discord Scrape Failed", e)
        return None


async def main():
    intents = Intents.default()          # ← includes the GUILDS intent
    intents.message_content = True       # you still need this for reading text
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        try:
            print(f"Logged in as {bot.user} – collecting URLs…")
            pairs = await gather_discord_urls(bot)
            print(f"Found {len(pairs)} D&D Beyond links")
            #Write Links to csv to avoid calling discord again
            with open("discord_links.csv", "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Thread Name", "Character URL"])
                for thread_name, url in pairs:
                    writer.writerow([thread_name, url])

            playwright = await async_playwright().start()
            sem = asyncio.Semaphore(CONCURRENCY)
            results = []

            async def bound_scrape(p):
                async with sem:
                    res = await scrape_character(p, playwright)
                    if res:
                        results.append(res)

            await asyncio.gather(*(bound_scrape(p) for p in pairs))
            await playwright.stop()

            # --- write csv ---------------------------------------------------
            with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f,
                        fieldnames=["url", "name", "species", "level", "cls", "subclass", "abilityscores", "savingthrows", "ac", "hp"])
                writer.writeheader()
                writer.writerows(results)
            # ----------------------------------------------------------------
            with open(FAILED_CSV_PATH, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f,
                        fieldnames=["url", "name"])
                writer.writeheader()
                writer.writerows(scrapefail)
            # ----------------------------------------------------------------

            print(f"Wrote {len(results)} rows ➜ {CSV_PATH}")
            await bot.close()
        finally:
            # This always runs, even if anything above crashed
            await bot.close()

    await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
