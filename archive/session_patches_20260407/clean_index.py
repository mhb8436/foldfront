import re

with open("frontend/index.html", "r") as f:
    content = f.read()

# Find the duplicate section starting from <div class="panel-divider"></div> before Filtering Cutoffs
pattern = r'(<div class="panel-divider"></div>\s*<div class="panel-header small">\s*<h4 data-i18n="evolution\.filtering\.title">Filtering Cutoffs</h4>.*?<textarea id="evolutionFixedPositionsExtraInput" rows="3" placeholder="A:6,10;\*:120"></textarea>\s*</label>)'

new_content = re.sub(pattern, '', content, flags=re.DOTALL)

if new_content != content:
    with open("frontend/index.html", "w") as f:
        f.write(new_content)
    print("Cleaned index.html duplicates.")
else:
    print("Could not find duplicates in index.html.")
