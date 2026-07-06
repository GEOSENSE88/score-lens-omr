# OMR Lens 서버 배포 (분석기와 독립)

모의고사 분석기(sannam_score)와 **완전히 분리된** 별도 서비스로 올린다.
분석기 nginx/파일은 건드리지 않는다. 접속 제어는 OMR Lens 자체 **접속 코드**.

⚠️ 이 앱은 업로드된 답안지와 생성된 성적표(학생 성명·점수)를 서버 디스크에
쓴다. `OMR_DATA_TTL_MIN` 으로 일정 시간 뒤 자동 삭제되지만, 인터넷 서버에
학생 데이터가 일시적으로라도 저장된다는 점을 감안할 것.

## 먼저 사람이 해야 할 것 (콘솔 권한 필요)

1. **DNS**: Cloudflare 에 `omr.26sannam3.site` A/CNAME → 서버 IP 추가
   (분석기와 같은 서버라면 같은 origin IP).
2. **방화벽**: 서버는 nginx(443)만 열면 됨 — 이미 열려 있으면 추가 작업 없음.
   앱 포트 5050 은 nginx 만 접근하므로 외부 개방 금지.

## 서버에서 (SSH 후)

```bash
# 1) 시스템 패키지 (opencv 런타임 라이브러리 포함)
sudo apt-get update
sudo apt-get install -y python3-venv libgl1 libglib2.0-0

# 2) 코드 배치 (분석기와 다른 경로)
sudo mkdir -p /opt/omr-lens && sudo chown $USER /opt/omr-lens
git clone https://github.com/GEOSENSE88/score-lens-omr /opt/omr-lens
cd /opt/omr-lens

# 3) 파이썬 환경
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt gunicorn

# 4) 접속 코드 설정 (8자리 권장)
echo "원하는8자리" > access_code.txt      # 또는 systemd 의 OMR_ACCESS_CODE

# 5) 정답키·등급컷 — 서버에서 웹 UI 의 'EBSi에서 정답 가져오기' 로 생성
#    (keys/ 는 저장소에 없음)

# 6) 서비스 등록
sudo cp deploy/omr-lens.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now omr-lens
sudo systemctl status omr-lens        # active (running) 확인

# 7) nginx (분석기 설정과 별개 파일)
sudo cp deploy/nginx-omr-lens.conf /etc/nginx/sites-available/omr-lens
sudo ln -s /etc/nginx/sites-available/omr-lens /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 8) TLS
sudo certbot --nginx -d omr.26sannam3.site
```

## 확인
- `https://omr.26sannam3.site` 접속 → 접속 코드 입력 화면
- 분석기(widget.26sannam3.site/score/) 정상 동작 그대로인지 확인
- 코드 변경: `access_code.txt` 수정 후 `sudo systemctl restart omr-lens`

## 업데이트
```bash
cd /opt/omr-lens && git pull && sudo systemctl restart omr-lens
```

## ⚠️ nginx 설정 변경 시 주의
`deploy/nginx-omr-lens.conf` 를 서버에 `cp` 로 덮어쓰면 **certbot 이 넣은 443
ssl_certificate 줄이 사라져 HTTPS 가 깨진다**(Cloudflare 526). 설정을 바꿨으면
반드시 이어서:
```bash
sudo certbot --nginx -d omr.26sannam3.site --non-interactive --reinstall --redirect
sudo nginx -t && sudo systemctl reload nginx
```
(client_max_body_size 같은 값만 바꿀 땐 서버 파일을 직접 sed 로 고치는 게 더 안전.)
