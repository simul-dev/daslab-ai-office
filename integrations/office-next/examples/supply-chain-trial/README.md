# 직원들이 납품한 합성 공급망 예제

2026-10-01 Paperclip DAS-3의 개발 → QA 반려 → 보완 → QA 수용 → PM 승인 과정에서 만들어진 파일의 원본 사본입니다. `artifact-provenance.json`에 출처와 SHA256을 남겼습니다. 운영 데이터·고객 자료·자격증명은 포함하지 않습니다.

`index.html`을 브라우저에서 열면 수요·비용·용량·비교 기준을 변경할 수 있습니다. 원본 수요는 130, 총비용은 A 1980 / B 790 / C 990입니다. 용량 80인 B를 제외하면 C가 최적이며 A 대비 50% 절감입니다. **합성 계산이며 실제 사업 효과·GIS·도로망·확률적 시뮬레이션 결과가 아닙니다.**

`integrations/office-next/examples`를 작업 폴더로 사용합니다.

```powershell
node --test supply-chain-trial/calculate.test.mjs
node supply-chain-trial/build.mjs
```

개발 테스트 34개와 별도 QA 계산 검증을 거쳤습니다. 실제 화면은 Codex 운영자가 CUA 브라우저에서 12개 시나리오를 관찰했고 QA와 PM이 같은 HTML 해시와 범위를 검토했습니다. 직원의 headless 브라우저 실행은 환경 문제로 실패했으며, 이 예제가 완전 무인 UI 검수를 입증하지는 않습니다. 자세한 실제 실행 기록은 [WORKER-RUN.md](../../docs/WORKER-RUN.md)를 참고하세요.
