<p align="center"><img src="static/img/logo.svg" alt="communactive" width="360"></p>

# communactive (커뮤넥티브)

디시인사이드 · 아카라이브 · 루리웹 같은 커뮤니티 사이트입니다.

## 기능
- **커뮤니티**: 회원이 직접 만드는 게시판 (`/c/<주소>`). **한 명당 하나만** 만들 수 있습니다 (관리자는 예외).
  - 맨 위에 인기글이 뜹니다 (최근 7일, 추천 3개 이상).
  - 전체글 / 인기글 탭, 검색, 추천·비추천, 글 수정·삭제
- **홈**: 모든 커뮤니티를 합친 **실시간(최근 3시간 동안 받은 추천) / 일간 / 주간 베스트**, 최신글
- **전체 검색**: 모든 커뮤니티의 글을 검색합니다.
- **포인트**: 글 작성 +200P, 댓글 +50P. 글이나 댓글을 지우면 받은 포인트를 회수합니다.
- **커뮤콘**: 회원이 이미지를 올려 직접 만들고 가격을 정합니다. 다른 회원은 포인트로 사서 댓글에 씁니다.
- **로그인**: 구글 / 네이버 / 페이스북 → 닉네임을 정하면 가입 완료. 계정 여러 개 연동, 회원 탈퇴 지원
- 이용약관(`/terms`), 개인정보처리방침(`/privacy`). 소셜 로그인 앱 심사에 필요합니다.

## 로컬 실행
```powershell
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # 처음 한 번. 값 채우기 (DEV_LOGIN=1 이면 개발용 로그인 사용 가능)
python app.py               # http://localhost:5000
```
로컬에서는 SQLite(`instance/communactive.db`)와 `static/uploads/`를 씁니다.

## 배포 (Vercel)
| 항목 | 사용 | 환경변수 |
|---|---|---|
| 서버 | Vercel Functions (Flask, `app.py`) | |
| DB | Neon Postgres (Vercel Marketplace) | `DATABASE_URL` |
| 커뮤콘 이미지 | Vercel Blob (public, `communactive-cons`) | `BLOB_READ_WRITE_TOKEN` |
| 세션 | | `SECRET_KEY` |
| 소셜 로그인 | | `GOOGLE_*`, `NAVER_*`, `FACEBOOK_*` |

```powershell
vercel --prod
```
각 소셜 개발자 콘솔에 콜백 URL `https://<도메인>/auth/{google,naver,facebook}/callback`을 등록하세요.
네이버·페이스북 앱 심사에는 개인정보처리방침 URL(`https://<도메인>/privacy`)이 필요합니다.

## 관리자 지정
관리자는 모든 글과 댓글을 삭제할 수 있고, 커뮤니티 개수 제한을 받지 않습니다.
```powershell
flask --app app make-admin <닉네임>
```
운영 DB에 적용하려면 `DATABASE_URL`을 운영 값으로 설정한 뒤 실행하세요.

## 파일 구조
| 파일 | 역할 |
|---|---|
| `app.py` | 앱 생성, 보안 헤더, 에러 페이지, 관리자 명령 |
| `config.py` | 설정 (포인트 액수, 인기글 기준, DB/Blob 감지) |
| `models.py` | DB 모델 |
| `auth.py` | 소셜 로그인 / 회원가입 / 계정 연동 / 탈퇴 |
| `board.py` | 커뮤니티, 글, 댓글, 추천, 검색 |
| `cons.py` | 커뮤콘 상점 / 만들기 / 구매 |
| `main.py` | 홈 (베스트), 마이페이지, 약관 |
| `utils.py` | 포인트, 베스트 집계, CSRF, 템플릿 필터 |
| `static/img/` | 로고, 파비콘, 공유 이미지 |
