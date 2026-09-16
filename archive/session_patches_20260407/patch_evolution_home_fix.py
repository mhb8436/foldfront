import re

with open('frontend/index.html', 'r') as f:
    html = f.read()

card_target = """                </button>
              </div>
              <div class="home-context-strip">"""
card_insert = """                </button>
                <button class="home-mode-card" type="button" data-home-target="evolution">
                  <h3 data-i18n="home.card.evolution.title">Evolution</h3>
                  <p data-i18n="home.card.evolution.desc">Autonomous multi-round sequence design pipeline.</p>
                </button>
              </div>
              <div class="home-context-strip">"""

if 'data-home-target="evolution"' not in html:
    html = html.replace(card_target, card_insert)
    with open('frontend/index.html', 'w') as f:
        f.write(html)
