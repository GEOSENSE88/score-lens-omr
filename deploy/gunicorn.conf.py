# OMR Lens — gunicorn 설정 (리버스 프록시 뒤 상시 구동)
# 실행: gunicorn -c deploy/gunicorn.conf.py web_app:app
bind = "127.0.0.1:5050"      # nginx 만 접근 (외부 직접 노출 금지)
workers = 2                   # 판독은 CPU 무거움 — 코어 수에 맞춰 조정
threads = 4
timeout = 600                 # 대량 PDF 채점이 길 수 있음
graceful_timeout = 30
max_requests = 200            # 메모리 누수 방지용 워커 재활용
max_requests_jitter = 40
accesslog = "-"
errorlog = "-"
loglevel = "info"
