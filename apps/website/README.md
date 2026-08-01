# apps/website — 공개 홈페이지

콘텐츠는 **`org/` 레지스트리에서 렌더된다** (`apps/groupware/dawn_groupware/site.py`).
사업을 추가하면 `org/businesses/*.yaml` 한 장으로 홈페이지에 반영된다 —
여기에 사본을 두지 않는다.

이 디렉터리는 나중에 정적 자산(이미지·다운로드 자료)이 생길 자리다.

```bash
make site        # 포그라운드
make web-bg      # 백그라운드 (그룹웨어와 함께)
```

접속: `http://<호스트 IP>:8810` — L0 공개 자산. 사내 데이터에 접근하지 않는다.
