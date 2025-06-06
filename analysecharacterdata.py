import csv
import re
from collections import defaultdict

def get_primary_class(class_field: str) -> str:
    """Given a class field like 'Cleric 1 / Wizard 8', return the dominant class."""
    parts = re.findall(r'([A-Za-z]+)\s*(\d*)', class_field)
    parsed = []
    for cls, lvl in parts:
        lvl = int(lvl) if lvl.isdigit() else 0
        parsed.append((cls.strip(), lvl))
    # Sort by level desc, then alphabetically
    parsed.sort(key=lambda x: (-x[1], x[0]))
    return parsed[0][0] if parsed else class_field.strip()

# Read original data
with open("characters.csv", newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

# Prepare simplified and sorted output
simplified = []
class_counts = defaultdict(int)
subclass_counts = defaultdict(lambda: defaultdict(int))

for row in rows:
    primary_class = get_primary_class(row["cls"])
    subclass = eval(row["subclass"])[0] if row["subclass"].startswith("[") else row["subclass"]
    
    simplified.append({
        "name": row["name"],
        "species": row["species"],
        "level": row["level"],
        "cls": primary_class,
        "subclass": subclass
    })
    
    class_counts[primary_class] += 1
    subclass_counts[primary_class][subclass] += 1

# Sort simplified output
simplified.sort(key=lambda r: (r["cls"], r["subclass"]))

# Write to CSV
with open("analysed_characters.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["name", "species", "level", "cls", "subclass"])
    writer.writeheader()
    writer.writerows(simplified)

# Print class/subclass counts
print("Character Counts:")
for cls in sorted(class_counts):
    print(f"- {cls}: {class_counts[cls]}")
    for subclass in sorted(subclass_counts[cls]):
        print(f"    - {subclass}: {subclass_counts[cls][subclass]}")