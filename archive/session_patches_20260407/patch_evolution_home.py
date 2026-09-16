import re

# 1. Update index.html
with open('frontend/index.html', 'r') as f:
    html = f.read()

card_target = '                </button>\n              </div>\n              <div class="panel-header home-header home-header-context">'
card_insert = """                </button>
                <button class="home-mode-card" type="button" data-home-target="evolution">
                  <h3 data-i18n="home.card.evolution.title">Evolution</h3>
                  <p data-i18n="home.card.evolution.desc">Autonomous multi-round sequence design pipeline.</p>
                </button>
              </div>
              <div class="panel-header home-header home-header-context">"""

if 'data-home-target="evolution"' not in html:
    html = html.replace(card_target, card_insert)
    with open('frontend/index.html', 'w') as f:
        f.write(html)

# 2. Update app.js i18n
with open('frontend/app.js', 'r') as f:
    js = f.read()

# En
en_target = '    "home.card.studio.desc": "Build and run a workflow stage by stage while watching the outputs.",'
en_insert = '    "home.card.studio.desc": "Build and run a workflow stage by stage while watching the outputs.",\n    "home.card.evolution.title": "Evolution",\n    "home.card.evolution.desc": "Autonomous multi-round sequence design pipeline.",'

if '"home.card.evolution.title"' not in js:
    js = js.replace(en_target, en_insert)

# Ko
ko_target = '    "home.card.studio.desc": "단계별 워크플로우를 구성하고 실행하면서 결과를 바로 확인합니다.",'
ko_insert = '    "home.card.studio.desc": "단계별 워크플로우를 구성하고 실행하면서 결과를 바로 확인합니다.",\n    "home.card.evolution.title": "진화 설계",\n    "home.card.evolution.desc": "다라운드 자동 유도 진화 파이프라인을 실행합니다.",'
if '"home.card.evolution.title": "진화 설계"' not in js:
    js = js.replace(ko_target, ko_insert)

# Update tabs.evolution Ko
js = js.replace('"tabs.evolution": "진화 (Evolution)",', '"tabs.evolution": "진화 설계",')

with open('frontend/app.js', 'w') as f:
    f.write(js)

