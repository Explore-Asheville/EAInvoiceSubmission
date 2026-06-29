"""
build_vendors.py - convert a vendor Excel/CSV into vendors.json for the form's
autocomplete. Run whenever the vendor list changes, then commit vendors.json
(Render redeploys on push).

Handles export files that have metadata rows above the real header: it scans the
first rows for the column header (Vendor name / Vendor / Payee / Supplier / Name).

Usage:
    pip install openpyxl
    python build_vendors.py "Vendor List.xlsx"           # auto-detects header + column
    python build_vendors.py "Vendor List.xlsx" --col "Vendor name"
    python build_vendors.py vendors.csv
"""
import sys, json, csv, argparse, pathlib

STRONG_KEYS = ("vendor name", "vendor", "payee", "supplier")
WEAK_KEYS = ("name", "company")
NAME_KEYS = STRONG_KEYS + WEAK_KEYS

def pick_col(header, col):
    if col:
        return header.index(col)
    low = [h.lower() for h in header]
    for key in NAME_KEYS:                      # exact match, strong keys first
        if key in low:
            return low.index(key)
    for i, h in enumerate(low):                # then "contains", strong keys first
        if any(k in h for k in STRONG_KEYS):
            return i
    for i, h in enumerate(low):
        if any(k in h for k in WEAK_KEYS):
            return i
    return 0

def find_header(grid):
    """Return (row_index, header) for the first real header row. Requires a name-ish
    column AND at least 3 non-empty cells, so metadata lines (e.g. 'Company name:')
    above the table are skipped."""
    for i, row in enumerate(grid[:15]):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if sum(1 for c in cells if c) < 3:
            continue
        if any(c.lower() in NAME_KEYS or any(k in c.lower() for k in NAME_KEYS) for c in cells):
            return i, cells
    return 0, [str(c).strip() if c is not None else "" for c in grid[0]]

def from_xlsx(path, col):
    import openpyxl
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
    grid = list(ws.iter_rows(values_only=True))
    hi, header = find_header(grid)
    idx = pick_col(header, col)
    return [str(r[idx]).strip() for r in grid[hi + 1:] if idx < len(r) and r[idx] not in (None, "")]

def from_csv(path, col):
    with open(path, newline="", encoding="utf-8-sig") as f:
        grid = list(csv.reader(f))
    hi, header = find_header(grid)
    idx = pick_col(header, col)
    return [r[idx].strip() for r in grid[hi + 1:] if idx < len(r) and r[idx].strip()]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--col", default=None, help="Exact column header to use")
    ap.add_argument("--out", default="vendors.json")
    a = ap.parse_args()
    p = pathlib.Path(a.path)
    names = from_csv(p, a.col) if p.suffix.lower() == ".csv" else from_xlsx(p, a.col)
    seen, uniq = set(), []
    for n in names:
        k = n.lower()
        if k not in seen:
            seen.add(k); uniq.append(n)
    uniq.sort(key=str.lower)
    json.dump({"vendors": uniq}, open(a.out, "w"), indent=0)
    print(f"Wrote {len(uniq)} vendors to {a.out}")

if __name__ == "__main__":
    main()
