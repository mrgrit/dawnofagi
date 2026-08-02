# Cloudflare Tunnel — 외부 접속 URL

이 호스트는 사설망(192.168.0.108)에 있다. 포트를 열거나 공유기를 건드리지 않고
외부에서 닿게 하려면 Cloudflare Tunnel 로 **밖으로 나가는 연결**을 만든다.
인바운드 방화벽 구멍이 없다 — 그게 이 방식을 쓰는 이유다.

```bash
make tunnel            # 공개 홈페이지(:8810) 를 연다  ← 기본
make tunnel-status     # 지금 열려 있는 것
make tunnel-down       # 전부 닫는다
```

## 무엇을 열 것인가 — 기본이 홈페이지인 이유

| 서비스 | 포트 | 인증 | 외부 노출 |
|---|---|---|---|
| 공개 홈페이지 | 8810 | 없음 | **원래 공개용.** `zone:ext` · `asset:site` · sec:L0 |
| 사내 그룹웨어 | 8811 | 로그인 필요 | 열면 무차별 대입 표적. `DAWN_PORTAL_HTTPS=1` 로 재기동할 것 |
| 픽셀 오피스 | 8800 | **없음** | ⚠ 전 에이전트 텔레메트리·케이스 제목·자산 이름·조직 구조가 그대로 보인다 |

```bash
make tunnel TARGET=portal    # 그룹웨어
make tunnel TARGET=office    # 픽셀 오피스 — 'open' 을 입력해야 열린다
```

픽셀 오피스만 확인 절차를 둔 이유: **인증이 없다.** URL 을 아는 사람은 누구나 본다.
Cloudflare 퀵 터널은 인증을 걸지 않는다 — 터널은 경로일 뿐 접근 통제가 아니다.

## 퀵 터널의 성질 (지금 쓰는 것)

- 계정 불필요. `cloudflared tunnel --url` 하나면 끝
- URL 은 **임의로 배정**되고 프로세스가 죽으면 사라진다 (`*.trycloudflare.com`)
- 무료·무보증. 데모·임시 공유용이다

## 고정 URL 이 필요하면 — 네임드 터널

자기 도메인이 있어야 하고, 로그인은 **브라우저가 필요해서 사람이 직접** 해야 한다.

```bash
cloudflared tunnel login                       # 브라우저 열림 — 사람이 실행
cloudflared tunnel create dawnofagi
cloudflared tunnel route dns dawnofagi www.<도메인>
```

`~/.cloudflared/config.yml`:

```yaml
tunnel: <터널-UUID>
credentials-file: /home/ccc/.cloudflared/<UUID>.json
ingress:
  - hostname: www.<도메인>
    service: http://127.0.0.1:8810
  # 그룹웨어를 열려면 Cloudflare Access 로 인증을 앞단에 두는 게 맞다
  # - hostname: portal.<도메인>
  #   service: http://127.0.0.1:8811
  - service: http_status:404
```

```bash
sudo cloudflared service install     # 부팅 시 자동 기동
```

**그룹웨어·픽셀 오피스를 고정 URL 로 열 거라면 Cloudflare Access(Zero Trust)를 앞에
붙여라.** 이메일 OTP·SSO 로 접근 자체를 막는다. 터널만으로는 아무것도 안 막힌다.

## 설치

```bash
ARCH=$(dpkg --print-architecture)
curl -fsSLo /tmp/cloudflared.deb \
  "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"
sudo dpkg -i /tmp/cloudflared.deb
```

## 파일

```
var/cloudflare/<target>.pid    프로세스
var/cloudflare/<target>.url    배정된 URL
var/cloudflare/<target>.log    cloudflared 로그
```

`var/` 는 gitignore 다 — URL 이 저장소에 들어가지 않는다.
