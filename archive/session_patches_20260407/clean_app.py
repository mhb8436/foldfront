import re

with open("frontend/app.js", "r") as f:
    lines = f.readlines()

# Remove lines 766-772
del lines[765:772]

with open("frontend/app.js", "w") as f:
    f.writelines(lines)
print("Cleaned el duplicates.")
