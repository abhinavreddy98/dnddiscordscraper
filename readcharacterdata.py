import csv

# Read the original CSV
with open("characters.csv", newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

# Sort by cls and then subclass
rows.sort(key=lambda r: (r["cls"], r["subclass"]))

# Write simplified CSV
with open("sorted_characters.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["name", "species", "level", "cls", "subclass"])
    for row in rows:
        writer.writerow([
            row["name"],
            row["species"],
            row["level"],
            row["cls"],
            row["subclass"]
        ])