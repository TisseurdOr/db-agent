"""进程级共享配置常量。

各入口（main.py / app.py / server.main）都在 load_dotenv() 之后才 import harness，
所以这里模块级读 os.getenv 是安全的。
"""

import os

DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "deepseek-chat")
