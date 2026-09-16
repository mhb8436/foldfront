import re

with open("frontend/app.js", "r") as f:
    lines = f.readlines()

# Remove the old i18n strings
start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if '"evolution.filtering.title": "Filtering Cutoffs",' in line:
        start_idx = i
    if '"evolution.fixedPositionsExtra.label": "Fixed Positions Extra",' in line and i > start_idx and start_idx != -1:
        end_idx = i

if start_idx != -1 and end_idx != -1:
    del lines[start_idx:end_idx+1]
    print("Cleaned English i18n duplicates.")

start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if '"evolution.filtering.title": "필터링 임계값",' in line:
        start_idx = i
    if '"evolution.fixedPositionsExtra.label": "고정 위치 추가",' in line and i > start_idx and start_idx != -1:
        end_idx = i

if start_idx != -1 and end_idx != -1:
    del lines[start_idx:end_idx+1]
    print("Cleaned Korean i18n duplicates.")

with open("frontend/app.js", "w") as f:
    f.writelines(lines)
