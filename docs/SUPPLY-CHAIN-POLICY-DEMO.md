# 공급망 정책 비교 데모 · 실행 기준

2026-09-30 대표가 데모 구현부터 PM 검수까지 직접 진행하도록 지시했다. 기존 정기 조사와 PM 검토를 이어받아 다음 작은 개발 단위를 수행한다. 책임자는 DAS-RD, 결과 검수는 DAS-PM이다. 실제 고객 성과·3D 완성·외부 게시를 이번 계산 데모와 구별한다.

## 이번 결과

입고 지연 상황에서 P0 기본 정책, P1 안전재고 목표 증가, P2 기존 정규 발주의 긴급운송 전환을 같은 합성 수요·같은 초기 물리재고·같은 지연 조건으로 비교한다. 기존 조사의 60일 모델은 이번에 14일짜리 검증 가능한 초안으로 좁힌다. 단일 품목·주문당 1개이며 입력·가정·납기·비용의 정의와 한계를 화면과 README에 남긴다. 입력을 바꾸면 실제 계산과 비교 표·상태 시각화가 바뀌어야 한다. 외부 연결이나 홈페이지 반영은 포함하지 않는다.

기존 `DASModel.run/defaults/checks`와 기존 입력·결과 DOM 계약은 보존한다. 아래 정책 비교 API를 추가한다. 기존 기본 검사만 통과한 것은 정책 비교 검증 완료가 아니다.

## 정책 비교 계약 v1

`window.DASModel.comparePolicies(input={})`는 순수하고 결정적인 함수다. 입력은 `days`(기본 14), `ordersPerDay`(6), `initialStock`(12), `seed`(42), `safetyStockDays`(2), `emergencyCapacity`(6). 무수요와 안전재고 추가량·긴급 용량 0을 지원한다. 지원 범위를 화면에 표시하고 잘못된 값을 거절한다.

반환값은 `{parameters, demandTrace, shockTrace, policies}`다.

- `demandTrace`: 모든 정책에 같은 `{id, arrivalAt, dueAt, quantity:1}` 배열. 건수는 `days*ordersPerDay`다.
- `shockTrace`: 공통 합성 지연 조건 기록 배열. 수요 난수와 정책 행동이 서로 다른 정책의 외생 조건을 바꾸지 않는다.
- `policies`: 정확히 P0/P1/P2의 배열. 각 항목은 `{policy, orders, purchaseOrders, receipts, stateLog, metrics, costs}`다.
- `orders`: 공통 수요와 같은 id의 `{id, reservedAt, shippedAt, deliveredAt}`. 아직 수행하지 않은 시점은 `null`이다.
- `purchaseOrders`: `{id, orderedAt, quantity, regularDepartureAt, regularArrivalAt, emergencyQuantity, emergencyDepartureAt, emergencyArrivalAt}`. 긴급운송을 사용하지 않으면 `emergencyQuantity=0`, 긴급 시각은 `null`이다. 기존 발주의 출발 전 전환이며 정규 물량은 `quantity-emergencyQuantity`다.
- `receipts`: 기간 내 실제 입고만 `{id, purchaseOrderId, mode:'regular'|'emergency', time, quantity}`로 남긴다. 발주·운송 방식당 입고는 최대 1회다. 긴급 전환한 물량을 정규편에서 다시 입고하지 않는다.
- `stateLog`: 시각 0의 초기 상태부터 시각 `days`의 종료까지 모든 재고·주문 상태 전이 뒤 `{time,onHand,reserved,backlog,onOrder,inventoryPosition,received,shipped,completed}`를 남긴다. 시각은 오름차순이며 같은 시각의 사건 순서를 고정한다.
- `onHand`는 예약분을 포함하는 물리재고, `backlog`는 미예약 대기만 뜻한다. `inventoryPosition=(onHand-reserved)+onOrder-backlog`다. 예약 주문을 backlog에서 다시 빼지 않는다. 재고 보존은 `initialStock+received=onHand+shipped`다.
- `metrics`: `{arrivals,completed,backlog,reserved,inTransit,otif,meanOrderDays,holdingUnitDays}`. OTIF는 기한 내 배송 완료/전체 유입 주문으로 계산해 기말 미완료도 분모에 포함한다. 무수요 OTIF는 0이다. 완료 평균 시간은 완료한 주문만 사용한다.
- `costs`: `{holdingRate,regularUnitRate,emergencyUnitRate,orderFee,holding,regular,emergency,ordering,total}`. 보유비는 stateLog의 onHand 시간적분×holdingRate, 정규·긴급비는 발주된 각각 물량×해당 단가, 발주비는 발주 건수×orderFee다. 기말 운송 중 물량의 발주 비용도 포함한다. total은 네 비용의 합이다. 합성 단가·단위를 명시한다.

P1은 기본 목표에 `ordersPerDay*safetyStockDays`를 추가한다. P2는 P0와 같은 목표를 사용하면서 출발 전 기존 발주만 긴급편으로 전환한다. `safetyStockDays=0`이면 P1의 실질 결과가 P0와 같아야 하고 `emergencyCapacity=0`이면 P2가 P0와 같아야 한다. 기본 합성 지연 시나리오에는 실제 긴급 전환이 있어야 한다. 특정 정책의 우위를 하드코딩하지 않는다.

정책 결과 영역은 `#policy-comparison`이며 각 정책 `[data-policy='P0']` 등의 요소에 실제 계산한 `data-otif`, `data-total-cost`를 둔다. 원시 추적·검사 결과를 확인할 수 있게 하고, 기존 SVG 상태도와 새 비교 표가 서로 다른 결과를 같은 것처럼 보이지 않게 구분한다. 3D가 아니면 3D라고 표기하지 않는다.

## 검수

개발 직원의 자체 검사와 서버가 수행하는 독립 계산·브라우저 검사를 구분한다. 서버는 주문·입고·상태 로그에서 보존식, 예약 이중 차감, 긴급 전환 중복 입고, OTIF·비용을 재계산하고 동일 조건 재현성과 0 경계 조건을 검사한다. PM은 이 기록과 실제 산출물을 기준으로 수용·수정 필요·남은 한계를 보고한다. 외부 현장 데이터 적합성은 별도 검증이다.

## 검증한 결과 보존 · 2026-09-30

실제 R&D 실행 `0a5c2a4acdd443f2bb985478833c4a59`의 6개 파일을 [demos/supply-chain-policy](../demos/supply-chain-policy/)에 원본 바이트 그대로 보존했다. 개발 당시 README의 자체 검사 기록은 수정하지 않았다. [provenance.json](../demos/supply-chain-policy/provenance.json)에 파일별 SHA-256, 재검수 ID·시각·범위·한계를 기록했고, 폴더의 `.gitattributes`로 Git 줄바꿈 변환을 막아 해시를 유지한다.

[index.html](../demos/supply-chain-policy/index.html)을 브라우저에서 직접 열면 실행된다. 별도 서버·패키지 설치는 필요 없다. 상단에서 정책 입력을 바꾸고 ‘세 정책 비교 실행’으로 결과를 확인한다. 기존 재고·피킹 모형은 아래에 별도로 남아 있다. 생성된 `model.test.cjs`는 작업자의 격리 환경용 자체 검사이며 일반 호스트에서 실행하는 절차로 제공하지 않는다.

오늘 서버 재검수에서 독립 계산·실제 브라우저 검사 32개를 통과했다. 정책 비교는 기본·동일 입력 반복·긴급 용량 0·추가 안전재고 0·수요 0의 다섯 조건을 검사했다. 이어 사용자 브라우저에서도 긴급 용량 0에서 P2와 P0가 같아지는 결과, 기준 입력 복원, 가정 펼치기 동작을 직접 확인했다. 실제 고객 성과, 3D, 공개 배포와 PM 최종 수용은 이 검사와 별도 상태다.
