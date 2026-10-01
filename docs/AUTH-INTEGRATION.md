# 대표 전용 접속

## 현재 선택: PC에서 휴대전화 연결

2026-10-01 대표가 실제 휴대전화 GitHub 로그인 실패 뒤 다른 방안을 요청했다. 이 Cloudflare 계정의 Access 무료 플랜 활성화 화면은 카드와 무료 한도 초과 요금 동의를 요구했다. 결제 정보를 입력하거나 Access를 켜지 않았다. 아래 GitHub 내용은 이전 구현·검증 이력으로 남긴다.

새 경로는 신뢰된 이 PC의 내부 `127.0.0.1:8772` 화면에서 소유자가 직접 **휴대폰 연결**을 시작한다. 화면에서 자체 생성한 QR을 휴대폰 카메라로 스캔한다. 10분 동안 한 번만 쓰는 256비트 무작위 연결 값이 `https://ai-office.daslab.co.kr/login#pair=...`의 URL 조각으로 전달된다. 공개 포트 `127.0.0.1:8774`만 전용 Cloudflare tunnel에 연결하고, 브라우저는 URL 조각을 즉시 지운 뒤 같은 origin의 `/auth/pair`에 제출한다. 공개 8774에서는 연결 전 조직 API·정적 사무실 자원·SSE를 차단하고, 로그인된 기기에만 30일 기기 세션의 `Secure; HttpOnly; SameSite=Lax` 쿠키로 접근을 허용한다. 세션은 같은 작업본의 `data/owner-sessions.sqlite3`에 토큰 해시만 저장해 서버 재시작 뒤에도 유지하며 최대 4개로 제한한다. PC 화면의 **연결 해제**는 세션·대기 중인 QR을 모두 무효화한다. 연결 값·세션 원문을 로그·Git·채팅에 기록하지 않는다. 직원 정기 업무는 기존 8772와 같은 엔진·데이터를 사용한다.

`config/auth.ai-office.local.json`의 현재 모드는 `pairing`이며 공개 origin은 위 주소다. `scripts/Start-DasOffice.ps1`은 해당 모드의 익명 `/api/auth`와 private API 401을 확인한 뒤에만 전용 터널을 시작한다. GitHub Client ID·Secret은 이 모드에 필요하지 않고, 터널 시작 시에는 기존 Windows DPAPI 저장소의 TunnelToken만 필요하다. 일반 PowerShell에 Node가 없으므로 이 PC에서는 확인된 `-NodePath`를 유지한다.

기기 연결 범위의 HTTP 요청은 포트별 Host·Origin과 JSON 전용 헤더를 확인한다. 대기 중인 QR 닫기와 늦은 생성 응답은 서버에서 취소한다. 서버는 각 listener의 동시 처리 연결을 48개로 제한하고 요청 소켓에 15초 제한 시간을 둔다. SSE는 별도 최대 12개와 매 전송 전 세션 검사를 유지한다. 2026-10-01 격리 HTTP 검사는 코드 재사용·만료·재시작 뒤 세션 유지·로그아웃·전체 연결 해제·로컬 전용 연결 시작·공개 무인증 차단을 통과했다. QR 해독, 브라우저의 연결 취소 순서 두 경우, 390px 휴대전화 화면도 격리 검사했다. 전체 Python 555개 중 551개 통과·4개 건너뜀, 로그인 UI JavaScript 4개 통과였다. 실행 중인 GitHub 방식의 서버와 대표의 실제 휴대전화는 이 결과에 포함되지 않는다.

실제 연결 확인(2026-10-01): 기존 GitHub 서버 종료 뒤 남은 전용 tunnel 자식 프로세스를 정확한 PID·경로·명령으로 확인해 정리하고, 동일 작업본·`data`의 서버와 tunnel을 숨김 실행으로 다시 시작했다. 내부 8772·공개 인증 포트 8774는 모두 loopback이며, `https://ai-office.daslab.co.kr`의 `/login`과 `/api/auth`는 200, 익명 `/api/org`는 401이다. 내부 조직의 기존 미션 23건과 실행·대기 0건, 정기업무 API를 확인했다. 실제 HTTPS 경로에서 일회용 코드 연결 → private 조직·정기업무 API 200 → 같은 코드 재사용 403 → 로그아웃 뒤 private API 401을 확인했다. 휴대전화 크기 Edge 브라우저에서도 PC의 실제 QR 버튼부터 원격 로그인·로그아웃까지 성공했다. 검사 기기 세션은 모두 로그아웃해 세션 DB에 0건이다. 이는 실제 대표 휴대전화의 카메라·마이크 검증은 아니다. 대표는 로컬 PC 화면의 **휴대폰 연결**을 한 번 눌러 본인 휴대전화로 QR을 스캔하면 된다.

## 새 사무실의 휴대전화 연결 (2026-10-01)

`server.py --office-next` 또는 `scripts/Start-DasOffice.ps1 -OfficeNext`는 PC의 8772와 인증된 공개 접속의 첫 화면을 같은 Paperclip·Pixel 사무실로 연결한다. 기존 정기업무 API·데이터는 그대로 사용하고, 두 주소의 `/office.html`에서 이전 조직 화면과 기록을 연다. 기본 실행은 명시적 옵션이 없으면 기존 화면을 유지한다.

PC의 새 사무실 상단 **휴대폰 연결**에서 QR을 만든다. 이 버튼은 `/api/auth`가 로컬 모드와 `pairing_available`을 함께 확인한 경우에만 표시한다. 공개 접속과 단독 개발 포트 8790에는 연결 코드 발급 버튼을 제공하지 않는다. 공개 로그인 화면에는 AI 오피스가 켜진 PC에서만 사용하는 새 사무실 링크와 QR 연결 순서를 표시한다. 실제 DAS Lab 로고 두 파일만 로그인 전에도 읽을 수 있고, QR 생성 스크립트·업무 자료는 기존 인증을 유지한다. 새 로컬 업무 POST는 정확한 Origin과 두 UI 헤더를 모두 요구한다.

연결 순서는 HTTPS → 기존 8774 소유자 로그인 → `office/next_gateway.py` → loopback 8790의 제한된 화면·업무 API다. 3101 Paperclip 관리 API를 공개하거나 Cloudflare 터널의 목적지를 바꾸지 않는다. 게이트웨이는 정해진 GET 경로와 업무 생성 POST만 허용하고, 기존 세션·Host·Origin·JSON 헤더 확인 뒤 요청한다. 쿠키·토큰·전달 헤더는 내부 서버에 보내지 않는다. API의 로컬 링크는 공개 화면에서 제거하고 공급망 데모는 기존 sandbox CSP를 유지한다. 서버 재시작은 기존 세션 DB와 업무 데이터를 그대로 사용한다.

화면에 표시되는 신규 직원·업무는 Office Next의 실제 기록이다. 이전 직원의 업무 이력과 정기 배정을 새 엔진으로 옮겼다는 뜻은 아니다. 이전 기록은 별도 메뉴로 보존하며, 기기 내 마이크와 실제 휴대전화 사용감은 별도 확인 대상이다.

실행할 때는 기존 `integrations/office-next/Start-OfficeNext.ps1`로 새 사무실의 로컬 서비스를 준비한 뒤, 확인된 Python·Node·DPAPI 경로를 사용한 기존 인증 실행기에 `-OfficeNext -StartTunnel`을 함께 전달한다. 실행기는 구·신 사무실 private API가 모두 익명 401인지 확인한 뒤에만 터널을 시작한다. 기존 서버가 실행 중이면 먼저 실제 직원 업무와 프로세스 소유관계를 확인해야 한다.

실제 반영·검수(2026-10-01): 기존 직원 active/queued 0과 정확한 서버·실행기·전용 터널 소유관계를 확인한 후 같은 작업본·data로 재시작했다. 기존 미션 ID 23개가 유지됐고, 새 8772/8774 소유자는 PID 23652, 실행기는 34640이다. Paperclip 40552·bridge 4020은 재시작하지 않았다. 기존 HTTPS 브라우저의 pairing 세션으로 다시 연결 절차 없이 새 첫 화면에 들어가 직원 6명·완료 업무 4개, DAS-4 실제 보고, 공급망 데모, `/office.html`의 이전 기록을 확인했다. 최종 번들의 로그아웃은 303 이후 `/api/auth`의 실제 세션 종료를 재확인하고 로그인 화면으로 이동했다. 검수 세션만 로그아웃했으며 대표 기기 세션을 전체 해제하지 않았다.

검증 범위: 게이트웨이·인증 접점 18개, 기존 인증 HTTP 14개·pairing 5개·두 listener 8개, Pixel 24개와 PowerShell 실행기 검사를 통과했다. 인증 서버에 직접 요청한 익명 `/api/org`, `/api/office/snapshot`, 그래픽·데모는 401이었다. Python으로 공개 주소를 조회한 검사는 Cloudflare 1010으로 차단되어 성공 근거에서 제외하며 Cloudflare 보호 설정은 바꾸지 않았다. 위 공개 접속 결과는 실제 HTTPS 데스크톱 브라우저 관찰이다. 검수 세션 로그아웃 후 세션 테이블은 0건이었다. 재시작 전부터 있던 검수 브라우저 세션의 유지 확인과 대표 폰의 로그인 상태 확인은 다르며, 폰에서 로그인 화면이 나타나면 기존 PC 연결 절차가 필요하다. 내장 브라우저의 390px 설정이 실제 viewport에 적용되지 않아 모바일 크기 검수로 인정하지 않았다. 대표의 물리적 휴대전화 재확인과 마이크 검증은 별도다.

## PC·폰 첫 화면 통합과 브랜드 반영 검수 · 2026-10-01

대표가 PC에 이전 사무실이 보인다고 지적하고 실제 DAS Lab 로고·컨셉으로 새 UI 디자인을 요청했다. PC 8772 첫 화면도 새 사무실로 연결하고, 새 상단에 기존 로컬 전용 QR 발급·취소를 붙였다. 로고 원본·네이비/시안·밝은 업무 패널을 사무실·직원·보고·연결 안내에 적용했다. 쿼리가 붙은 첫 화면 북마크는 `/`로 정규화해 이전 화면으로 돌아가지 않는다.

실제 서버는 직원 실행·대기 0건과 정확한 프로세스·잠금 소유 확인 후 같은 data로 갱신했다. 최종 서버 PID 45556, 실행기 46708이며 기존 미션 ID 23개와 정기 배정의 paused=false를 보존했다. Paperclip 40552와 bridge 4020은 재시작하지 않았다. 최종 번들은 `index-BO66THYe.js` / `index-WvckXnLI.css`다.

실제 내장 브라우저에서 PC 새 첫 화면·로고·6명 직원·선택·업무 목록 4건·DAS-4 보고서·지시 초안의 제출 가능 상태와 삭제(전송하지 않음), QR 생성(canvas 1개)·닫기·재발급을 확인했다. 공개 HTTPS에서는 새 브랜드 로그인 화면과 PC 진입 안내를 확인했다. 익명 인증 포트의 구·신 업무 API 및 QR 스크립트는 계속 401이다. Python 접점 검사 59개 중 58개 통과/1개 건너뜀, Pixel 40개·로그인 JavaScript 4개 통과다.

이번 검수에서 일회용 주소를 브라우저로 여는 단계는 브라우저 URL 정책이 ‘invalid URL’ 사유로 차단하여 중단했고 우회하지 않았다. 따라서 이번 새 QR에서 공개 로그인 완료까지 검증했다고 주장하지 않는다. 폰의 실제 카메라 스캔은 남아 있다. 내장 브라우저의 실제 폭은 644px였고 390px viewport 요청은 적용되지 않아 물리적 폰이나 390px 검수로 인정하지 않았다. 앞선 실제 HTTPS 로그인 검증 이력과 이번 UI 검수 범위를 구분한다.

## 이전 GitHub 방식의 구현·검증 기록

`office/auth.py`는 GitHub 개인 계정의 숫자 ID 한 개만 허용한다. 사용자 이름이 바뀌어도 같은 ID이면 유지되고, 같은 이름을 쓰는 다른 ID는 거절한다. 조직 전체의 가입·접근 권한은 제공하지 않는다. 계정 로그인은 사무실 접속을 허용하며 직원 도구·배포·비용 권한을 추가하지 않는다.

## 설정과 실제 연결에 필요한 자료

대표가 선택한 주소는 `https://ai-office.daslab.co.kr`이다(2026-10-01). 준비한 로컬 설정 `config/auth.ai-office.local.json`은 이 주소와 확인된 대표의 GitHub 숫자 ID 한 개를 사용하며 Git에서 제외한다. 기본 서버는 이 파일을 자동으로 읽지 않는다. 실제 OAuth 등록·환경변수·HTTPS 연결이 준비되면 `--auth-config config/auth.ai-office.local.json`으로 명시적으로 전환한다. 설정 준비는 DNS 등록이나 서비스 공개를 뜻하지 않는다.

이번 GitHub OAuth App 등록값:

| 항목 | 값 |
| --- | --- |
| Application name | DAS Lab AI Office |
| Homepage URL | `https://ai-office.daslab.co.kr` |
| Authorization callback URL | `https://ai-office.daslab.co.kr/auth/callback` |

현재 DNS의 권한 네임서버는 Cloudflare(`uriah.ns.cloudflare.com`, `holly.ns.cloudflare.com`)로 확인했다. 이 PC를 실행 호스트로 계속 쓸 경우, 전용 이름이 고정된 Cloudflare Tunnel의 published application을 이 서브도메인과 `http://127.0.0.1:8774`로 연결한다. 기존 다른 서비스의 tunnel은 재사용하지 않는다. 공개 연결 전에 GitHub 전용 포트를 시작해, canonical Host에서 로그인 없는 `/api/org`가 401인 것을 확인한다. HTTP Host Header는 `ai-office.daslab.co.kr`로 지정하며 localhost로 바꾸지 않는다. 메인 홈페이지의 루트 DNS 레코드는 수정하지 않는다. PC·오피스 서버·tunnel이 켜져 있는 동안만 이용 가능하다. OAuth 비밀키와 tunnel token은 채팅·Git·공유 문서에 넣지 않는다.

`--port 8772 --public-port 8774 --auth-config config/auth.ai-office.local.json`은 한 프로세스·한 조직 엔진에 두 loopback 접속 경로를 연다. 8772는 이 PC의 기존 관리·직원 정기 업무용이며 기존 Host·Origin·접속자 검사를 유지한다. 8774는 GitHub 로그인 전용으로 Cloudflare가 연결할 유일한 포트다. 두 포트는 같은 업무 데이터와 실행기를 사용한다. GitHub 설정이 없거나 포트가 같으면 이 구성은 시작하지 않는다. 로그인 없는 8772를 tunnel에 연결하지 않는다. `--public-port`가 없으면 기존 단일 포트 실행 방식이 유지된다.

서버 기본값은 기존 `local` 모드다. 로그인 없는 접속은 loopback 바인드·접속자·Host에만 허용된다. 외부 주소를 열려면 `github` 모드와 정확한 HTTPS 공개 origin, 대표 GitHub 숫자 ID, 별도로 등록한 OAuth App의 Client ID·Secret이 필요하다. 실제 계정 로그인은 해당 등록과 공개 URL을 연결한 뒤 별도로 검증해야 한다.

`config/auth.example.json`을 로컬 `config/auth.json`으로 복사하고 공개 origin과 `allowed_github_ids`의 숫자 ID 한 개를 채운다. 빈 ID 목록은 모든 계정을 거절하며 GitHub 모드 시작을 실패시킨다. Client ID·Secret 값은 파일에 넣지 않고 각각 `DAS_OFFICE_GITHUB_CLIENT_ID`, `DAS_OFFICE_GITHUB_CLIENT_SECRET` 환경변수로 전달한다. 기존 Git·CLI 토큰을 복사하거나 재사용하지 않는다. 실제 설정 파일·환경변수·쿠키·OAuth 응답은 Git과 실행 보고서에 넣지 않는다.

OAuth App의 Homepage URL은 공개 origin, callback은 정확히 `<공개 origin>/auth/callback`으로 등록한다. 예: `https://office.example.com/auth/callback`. Callback wildcard는 사용하지 않는다. 서버는 loopback에 두고 HTTPS reverse proxy 또는 tunnel이 공개 origin의 Host를 보존해 전달하도록 연결한다. 이 모듈은 TLS 인증서 발급·proxy 설치·배포를 수행하지 않으며 `X-Forwarded-Host`나 `X-Forwarded-Proto`를 신뢰해 주소를 바꾸지 않는다. 공개 HTTPS 연결이 준비되지 않았다면 휴대전화 접속까지 완료했다고 하지 않는다.

## Windows의 비밀키 보관과 실행

PowerShell 7.2 이상에서 `scripts/Save-DasOfficeCredentials.ps1 -Kind GitHub`를 실행하면 Client ID와 Secret을 가려진 입력창으로 받는다. `-Kind Tunnel`은 전용 tunnel token만 추가하며 기존 GitHub 값을 보존한다. 값을 명령 인자·채팅·저장소에 붙여넣지 않는다. 기본 저장 위치는 `%LOCALAPPDATA%\DASLab\ai-office\credentials.clixml`이며 Windows 사용자별 DPAPI 암호화와 해당 사용자·SYSTEM 접근 제한을 적용한다. 다른 PC나 Windows 계정에서는 다시 입력해야 한다.

`scripts/Start-DasOffice.ps1 -PythonPath <프로젝트 venv의 python.exe>`는 이 스크립트가 속한 작업본과 그 `data`로 8772·8774를 함께 연다. `-StartTunnel`을 명시한 경우만 `%LOCALAPPDATA%\DASLab\tools\cloudflared.exe`를 실행한다. 기존 포트를 쓰는 프로세스가 있으면 그대로 두고 중단한다. 전용 포트의 `/api/auth`가 GitHub 인증 필수·미로그인 상태임을 확인한 뒤에만 tunnel을 시작한다. OAuth 값과 tunnel token은 각각 필요한 자식 프로세스의 환경에만 전달하고 부모 셸·프로세스 명령줄에 넣지 않는다. launcher는 두 프로세스를 함께 관리하며 자동 시작 작업이나 Windows 서비스를 설치하지 않는다.

정기 업무 예약은 계속 `http://127.0.0.1:8772`를 사용한다. 서버 재시작 시 위 launcher와 같은 작업본·데이터를 유지한다. GitHub 연결을 이미 운영 중인 환경에서 일반 `python server.py`로 대체하거나 로그인 전용 포트를 local 모드로 시작하지 않는다. 운영 중인 업무가 있으면 재시작 전에 실제 상태와 데이터 잠금을 확인한다.

일반 PowerShell의 PATH에 Node가 없다면 `-NodePath <설치된 node.exe 절대경로>`도 전달한다. 2026-10-01 정기 실행에서 인증 서버는 정상이지만 데모 검사가 `Node.js unavailable: syntax inspection cannot run`으로 실패한 사례를 확인했다. 실행기는 지정 파일을 확인하고 서버 자식의 PATH에만 그 디렉터리를 추가하며 부모 셸과 tunnel PATH는 바꾸지 않는다. 이 PC의 수동 재시작 안내에는 확인된 Codex runtime의 `dependencies\node\bin\node.exe`를 명시했다. 수정은 Node 없는 부모 환경의 격리 실행 6시나리오와 잘못된 경로 5종으로 검증했으며, 기존 운영 서버에 적용하려면 별도 재시작이 필요하다. 이 수정은 GitHub 실제 로그인 성공이나 GIS 정기 후속 개발 연결 완료를 뜻하지 않는다.

## 서버 연결 계약

- `OfficeAuth(config=None, environ=None, http=None, clock=None)`의 기본값은 `local`이다. `environ`·`http`·`clock`은 테스트 대체 지점이며 일반 서버는 전달하지 않는다. 실제 키워드 인자는 `config` 뒤에서만 사용한다.
- `validate_bind(host)`는 local 모드의 외부 바인드를 거절한다. `check_request(host, origin, peer, port, write=False)`는 모든 경로에서 먼저 호출한다. GitHub 모드의 Host는 `public_origin`과 같아야 하며 POST의 Origin도 정확히 같아야 한다. 기존 JSON·`X-DAS-Office: 1`·본문 크기 제한도 유지한다.
- `begin_login(cookie_header="")`에 현재 Cookie 헤더를 전달하고, 반환한 `location`으로 이동하며 `cookies: list[str]`를 각각 별도 `Set-Cookie` 헤더로 보낸다. 같은 브라우저의 여러 로그인 탭은 기존 결합 쿠키를 공유하므로 로그아웃이 늦은 다른 탭의 응답도 취소할 수 있다. `finish_login(raw_query, cookie_header)`는 raw query의 중복 인자까지 검사한다. 성공 응답의 `location`은 항상 `/`이다. 임의의 다음 URL이나 callback host를 받지 않는다.
- `current_user(cookie_header)`가 없으면 private API·파일·미리보기·SSE를 거절한다. 로그인 UI·시작·callback·공개 인증 상태만 예외로 둔다. SSE는 연결 시작과 반복 전송마다 만료·로그아웃을 다시 확인한다. 인증 상태 JSON에는 `mode`, `required`, `authenticated`, `user`, `login_url`만 공개된다.
- `logout(cookie_header)`는 서버 세션과 해당 브라우저의 미완료 로그인 요청을 지우고 만료 쿠키를 돌려준다. 서버는 CSRF 검사를 통과한 POST에만 연결한다. `auth.mode`와 `auth.public_origin`은 읽기용 공개 속성이다.

## 구현 경계

GitHub authorization code flow에서 무작위 state, 별도의 브라우저 결합 쿠키, S256 PKCE를 함께 사용한다. 요청은 10분 안에 한 번만 교환할 수 있다. Client Secret은 token endpoint의 POST 본문으로만 보내며 응답 redirect를 따라가지 않는다. `read:user`만 요청하고 더 넓은 scope의 응답은 거절한다. 매번 `/user`에서 숫자 ID를 확인한 뒤 OAuth access token을 애플리케이션 저장소·세션·응답에 보관하지 않는다.

사무실 세션은 GitHub 토큰과 별개의 무작위 값이다. 서버 메모리에는 세션·브라우저 결합 값의 해시와 사용자 ID·로그인 이름·만료 시각만 저장한다. 쿠키는 `__Host-` 이름, `Path=/`, `Secure`, `HttpOnly`, `SameSite=Lax`이고 Domain은 지정하지 않는다. 기본 만료는 8시간(설정 허용 5분~12시간)이며 재로그인 때 현재 세션을 교체한다. 로그아웃은 해당 브라우저의 진행 중인 GitHub 응답과 아직 브라우저에 도착하지 않은 새 세션도 무효화한다. 서버 재시작·로그아웃·만료는 재로그인이 필요하다. 발급 뒤 GitHub 측 로그아웃이나 권한 철회를 실시간 구독하지 않으므로 이미 발급한 사무실 세션은 자체 만료까지 별도로 유효하다.

로그인 대기는 최대 128개이며 가득 차면 가장 오래된 미사용 요청부터 버려 새 로그인을 계속 받을 수 있게 한다. GitHub 응답 대기는 동시에 최대 16개, 사무실 세션은 최대 64개다. 이는 메모리 제한이며 지속적인 익명 요청의 가용성 방어를 대신하지 않는다. 공개 reverse proxy에는 `/auth/login`과 `/auth/callback`의 IP별 요청률·동시 연결 제한을 설정해야 한다. 외부 서비스의 상세 오류·토큰·쿠키를 오류 메시지에 넣지 않는다. 단위 검사는 가짜 HTTP 응답으로 수행하며 GitHub 실제 로그인·휴대전화 연결의 근거를 대신하지 않는다.

확인한 공식 규격: GitHub의 [OAuth App authorization code·state·S256 PKCE 및 callback 규칙](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps), [인증된 사용자 조회 API](https://docs.github.com/en/rest/users/users#get-the-authenticated-user). 확인일: 2026-10-01.

## 현재 연결 범위와 검증 · 2026-10-01

- 최신 로그인 수정: 수동 재시작으로 진단 코드를 적용한 실제 로그인 실패 원인은 `callback_invalid`였다. GitHub의 [공식 OAuth metadata](https://github.com/.well-known/oauth-authorization-server/login/oauth)는 `iss` 응답 지원과 `https://github.com/login/oauth` 발급자를 명시하지만 기존 코드는 `code`·`state` 외 인자를 모두 거절했다. 동일 형식의 격리 HTTP 응답으로 수정 전 실패를 재현하고, `iss`를 필수·단일·정확 일치로 검증하도록 수정한 뒤 세션 발급과 private API 200을 확인했다. 인증·HTTP·두 listener 관련 43개가 통과했으며 기존 state·브라우저 결합·PKCE·허용 계정 검증은 유지한다. 실행 중인 서버에는 이 수정의 수동 적용이 남아 있으므로 실제 로그인·로그아웃 성공으로 보고하지 않는다. 아래 키 오류와 진단 미적용 기록은 이전 경과다.
- 대표의 수동 재시작으로 같은 작업본·데이터를 쓰는 내부 8772와 GitHub 전용 8774, 전용 Cloudflare tunnel이 실행됐다. `ai-office.daslab.co.kr`의 CNAME과 published application을 등록했고 실제 HTTPS 로그인 화면을 확인했다. 서비스 URL은 `http://127.0.0.1:8774`, HTTP Host Header는 `ai-office.daslab.co.kr`이다. 기존 메인 도메인과 `daslab` tunnel은 변경하지 않았다. 업무 19건과 데이터 잠금, 내부 조직·정기업무 GET의 정상 응답을 확인했다.
- 실제 GitHub 로그인은 아직 미완료다. OAuth App 3894492의 `simul-dev` 계정에서 읽기 전용 프로필 승인 화면까지 진행했지만 callback 로그인이 실패했다. 저장된 Client ID·Secret 조합을 고정된 유효하지 않은 code로 별도 진단했을 때 GitHub가 `incorrect_client_credentials`를 반환했다. 이는 실제 로그인 검증이 아니며, 대표의 키 재입력·수동 재시작 후 로그인·로그아웃을 다시 확인해야 한다. 비밀 값과 요청·응답 원문은 기록하지 않았다.
- 후속 상태: 대표가 새 키를 직접 입력했고 고정 invalid-code 진단에서 .NET·동일 Python의 응답 모두 `bad_verification_code`로 바뀌어 잘못된 키 조합 문제는 해결됐다. 수동 재시작 뒤에도 내장 브라우저의 실제 로그인은 실패했고 대표도 폰에서 실패한다고 확인했다. 실제 로그인 성공·로그아웃 검증은 계속 미완료다. 추가한 고정 원인 코드(`AuthError.reason` → 로그인 페이지의 `data-reason`)는 다음 수동 재시작 뒤 적용된다. 쿠키·state·토큰 교환·응답 형식·scope·사용자 조회 단계를 구분하며 원문이나 비밀 값을 공개하지 않는다. 관련 인증·HTTP 32개와 로그인 UI의 원인 허용목록·URL 정리 검사 3개를 통과했다. 원인 분류 기능 자체는 로그인 실패 수정이나 성공의 증거가 아니다.
- 이 PC의 Codex 패키지는 `LOCALAPPDATA`를 MSIX 저장 폴더로 연결한다. 일반 PowerShell에서 같은 파일을 사용하려면 확인된 `C:\Users\User\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\DASLab` 아래 `ai-office\credentials.clixml` 및 `tools\cloudflared.exe`를 각각 `-CredentialPath`, `-CloudflaredPath`로 전달한다. apparent 경로와 실제 경로가 동일 파일임을 파일 ID로 확인했다. 대표의 수동 입력용 안내 파일은 Git에서 제외한 `test-results/Repair-DasOfficeLogin-Manual.ps1`에 준비했으며 에이전트가 재시작 차단을 우회해 실행하지 않았다.
- 직접 8774 검사에서는 정확한 Host의 익명 private API가 401, 잘못된 Host가 403이고 허용 ID는 `[28843356]` 한 개다. 별도 cookie-less HTTPS 클라이언트에서는 403을 받았으므로 이를 origin의 401 검증으로 표현하지 않는다. 실제 로그인 성공, 다른 계정의 실접속 거절, 대표 폰의 접속·마이크는 아직 확인하지 않았다.
- 조직 화면·API·음성 파일은 GitHub 모드에서 세션이 있어야 열린다. 로그아웃·만료 뒤 SSE는 다음 전송 전에 중단한다. 자동 재로그인 때 미전송 초안·직원 선택·중복 접수 방지 ID는 같은 탭의 sessionStorage에 잠시 보존하고 본인 로그인 뒤 복구한다(24시간 한도). 명시 로그아웃은 초안을 지운다.
- 휴대전화 마이크는 브라우저의 [보안 컨텍스트 요건](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)을 따른다. 별도 PC loopback 포트에서 열리는 개발 미리보기·데모는 원격 모드에서 안내 후 차단한다. 생성된 데모 JavaScript를 인증된 사무실과 같은 origin으로 프록시하지 않는다. 원격 데모 열기는 별도 격리 배포가 필요한 후속 기능이다.
- 인증·HTTP·호칭 자동 선택은 가짜 GitHub 응답과 격리 서버로 검사했다. 다른 숫자 ID·잘못된 Host/Origin·중복/재사용 callback·위조 cookie를 거절하고, 로그아웃 중인 callback과 다른 탭의 늦은 응답도 세션을 발급하지 못하는지 확인했다.
- 메인 화면에서 PM/마케팅 직접 호칭, 수동 직원 선택 우선, 복수 직원 선택 안내를 브라우저로 확인했다. 390×844 화면에서 가로 넘침·오류 로그 없이 입력창을 확인했다. 실제 업무 전송이나 직원 실행은 이 UI 확인에서 하지 않았다.
- 신규 인라인 컨트롤러 13개와 기존 음성 정책 16개는 합성 PCM·가짜 마이크로 검사했다. 버튼 시작·취소·60초 자동 전사·숨김 탭·늦은 권한/전사·로그아웃을 확인했다. 대표 음성 녹음은 하지 않았다.
- `tests/inline_voice_runtime_server.py`와 기존 SHA-256 고정 공개 WAV 샘플로 실제 브라우저 Worker의 `init-asr`와 `transcribe-inline`을 실행했다. 3.3초 샘플, 초기화 2.1초·전사 3.9초·총 6.0초로 비어 있지 않은 전사문을 받았다. 이는 PC에서 호출이 작동한다는 근거이며 한국어 정확도·대표 음성·폰 성능을 검증한 결과는 아니다.
- 기존 PM 미리보기는 음성 JS를 변경할 수 없는 해시 고정 파일로 복사한다. 미리보기의 microphone·Worker·POST 실행은 차단한다. 음성 런타임 없는 예전 미리보기와 저장 기록은 유지한다. 개발·전달·PM 관련 회귀 104개 통과·1개 건너뜀, 실제 격리 브라우저의 직원 선택·키보드·데스크톱/모바일·음성 비활성 검수를 통과했다.
- 최종 Python 전체 회귀는 523개 중 519개 통과·4개 건너뜀(186.1초). JavaScript 음성 검사는 29개 모두 통과했다. 검사 로그와 PC 공개 샘플 추론 기록은 로컬 `test-results/`에 보관하고 운영 DB·로그·모델은 Git에 포함하지 않는다.
- 이후 추가한 두 접속 경로는 실제 loopback HTTP에서 같은 엔진의 상태 공유, local 정기 호출 유지, GitHub 세션·Host·Origin 경계와 실패 시 정리를 검사했다(신규 8개와 기존 인증 26개 통과). 서버는 인증 객체가 값을 읽은 직후 OAuth·tunnel 환경변수를 제거하고, Git·Node·브라우저에도 정제한 환경만 전달한다. 실제 자식·손자 프로세스와 Playwright driver 검사 및 관련 회귀는 55개 통과·기존 2개 건너뜀이다.
- Windows 스크립트는 DPAPI 저장·복구, 두 종류의 키 개별 갱신, 제한 ACL, 평문 미보관, 잘못된 입력·취소 때 기존 파일 보존을 검사했다. 가짜 서버·tunnel로 정상/잘못된 인증/잘못된 상태값/조기 종료/tunnel 실패/포트 점유 여섯 경계를 확인했다. 실제 키·운영 서버·외부 연결을 사용한 검증은 아니다.
