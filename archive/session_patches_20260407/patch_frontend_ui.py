import re

with open("frontend/app.js", "r") as f:
    code = f.read()

# 1. Add fields to `normalizeQuestions` so they are known
t1 = '      type: "boolean",\n    },\n  };'
i1 = '      type: "boolean",\n    },\n    evolution_mode: {\n      type: "boolean",\n    },\n    evolution_initial_samples: {\n      type: "number",\n      default: 20,\n    },\n    evolution_rounds: {\n      type: "number",\n      default: 3,\n    },\n    evolution_samples_per_round: {\n      type: "number",\n      default: 5,\n    },\n  };'
code = code.replace(t1, i1)

# 2. Render these fields in the Advanced Setup modal
t2 = '  const defaultGroups = {'
i2 = '  const defaultGroups = {\n    "Evolution (BO) Settings": ["evolution_mode", "evolution_initial_samples", "evolution_rounds", "evolution_samples_per_round"],'
code = code.replace(t2, i2)

with open("frontend/app.js", "w") as f:
    f.write(code)
