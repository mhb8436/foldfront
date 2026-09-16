import re

with open("frontend/index.html", "r") as f:
    lines = f.readlines()

start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if '<h4 data-i18n="evolution.filtering.title">Filtering Cutoffs</h4>' in line:
        # Go back to the panel-divider if it exists
        if i > 2 and '<div class="panel-divider"></div>' in lines[i-2]:
            start_idx = i - 2
        else:
            start_idx = i - 1
    if '<input id="evolutionFixedPositionsExtraInput" type="text" placeholder="e.g. A1-10, B20" />' in line:
        end_idx = i + 1

if start_idx != -1 and end_idx != -1:
    del lines[start_idx:end_idx+1]
    with open("frontend/index.html", "w") as f:
        f.writelines(lines)
    print("Cleaned index.html duplicates.")
else:
    print("Could not find duplicates in index.html.")
