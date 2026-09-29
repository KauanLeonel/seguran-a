bind = '0.0.0.0:8000'
# Rate limits are in memory: keep exactly one process, with bounded threads.
workers = 1
worker_class = 'gthread'
threads = 8
timeout = 60
graceful_timeout = 30
worker_tmp_dir = '/tmp'
forwarded_allow_ips = '172.30.44.10'
secure_scheme_headers = {'X-FORWARDED-PROTO': 'https'}
limit_request_line = 4094
limit_request_fields = 40
limit_request_field_size = 4096
accesslog = None
errorlog = '-'
