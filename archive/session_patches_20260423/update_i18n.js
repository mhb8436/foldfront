const fs = require('fs');

const koPath = 'frontend/lib/locales/ko.json';
const enPath = 'frontend/lib/locales/en.json';

const koData = JSON.parse(fs.readFileSync(koPath, 'utf8'));
const enData = JSON.parse(fs.readFileSync(enPath, 'utf8'));

// KOREAN
koData.question.evolutionPoolSize = {
    label: "Evolution 생성 풀 크기",
    help: "ProteinMPNN으로 초기 생성할 총 서열 개수 (기본 1000)"
};
koData.question.evolutionOracleSamples = {
    label: "Evolution 최종 AF2 검증 수 (Top K)",
    help: "대리 모델이 예측한 서열 중 실제로 AF2를 돌릴 상위 서열 개수 (기본 20)"
};
koData.question.evolutionInitialSamples.label = "Evolution 훈련용 오라클 예산 (N)";
koData.question.evolutionInitialSamples.help = "대리 모델을 훈련하기 위해 K-Means로 다양하게 뽑아 먼저 AF2를 돌릴 서열 개수 (기본 30)";

// ENGLISH
enData.question.evolutionPoolSize = {
    label: "Evolution Initial Pool Size",
    help: "Total number of sequences to generate with ProteinMPNN initially (default 1000)"
};
enData.question.evolutionOracleSamples = {
    label: "Evolution Final AF2 Budget (Top K)",
    help: "Number of top-predicted sequences to validate with AF2 (default 20)"
};
enData.question.evolutionInitialSamples.label = "Evolution Oracle Budget (N)";
enData.question.evolutionInitialSamples.help = "Number of diverse sequences to pick via K-Means and evaluate with AF2 for training the surrogate model (default 30)";

fs.writeFileSync(koPath, JSON.stringify(koData, null, 2));
fs.writeFileSync(enPath, JSON.stringify(enData, null, 2));
console.log("i18n translation files updated!");
