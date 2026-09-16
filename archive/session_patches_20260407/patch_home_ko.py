with open('frontend/app.js', 'r') as f:
    js = f.read()

target = '    "home.card.studio.desc": "단계를 보면서 워크플로우를 직접 구성하고 실행합니다.",'
insert = '    "home.card.studio.desc": "단계를 보면서 워크플로우를 직접 구성하고 실행합니다.",\n    "home.card.evolution.title": "진화 설계",\n    "home.card.evolution.desc": "다라운드 자동 유도 진화 파이프라인을 실행합니다.",'

js = js.replace(target, insert)

with open('frontend/app.js', 'w') as f:
    f.write(js)
