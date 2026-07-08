# OMR Lens — gunicorn 설정 (리버스 프록시 뒤 상시 구동)
# 실행: gunicorn -c deploy/gunicorn.conf.py web_app:app
bind = "127.0.0.1:5050"      # nginx 만 접근 (외부 직접 노출 금지)
# ⚠️ 반드시 단일 워커. 채점 작업상태(JOBS)·결과(RESULTS)가 프로세스 메모리에 있고
#    채점은 백그라운드 스레드로 돈다. 워커가 2개 이상이면 상태폴링·결과조회가 엉뚱한
#    워커로 라우팅돼 간헐적 404/HTML(502) 오류가 난다. 무거운 판독은 subprocess
#    (run.py 등)로 분리 실행되므로 단일 워커+스레드로 동시성 충분.
workers = 1
worker_class = "gthread"
threads = 8                   # 동시 접속·폴링 (판독 자체는 자식 프로세스에서)
timeout = 600                 # 대량 PDF 채점이 길 수 있음
graceful_timeout = 30
max_requests = 0              # 워커 재활용 금지 — 진행중 작업/결과 상태 유실 방지
accesslog = "-"
errorlog = "-"
loglevel = "info"
