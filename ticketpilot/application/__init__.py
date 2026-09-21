"""
application 层：跨入口的编排服务。

分层规则：application 可以 import 任何下层（core/data/tools/agent/rag）；
除入口（frontend/app.py、api/routes.py、main.py）和 tests 外，
任何模块不得 import application。
"""
