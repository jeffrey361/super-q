FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    PYTHONPATH=/app

ARG INSTALL_GM=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata gcc g++ \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./

RUN python -m pip install --upgrade pip \
    && python -m pip install akshare "pydantic-settings>=2.0" "python-dotenv>=1.0" "rich>=13.0" "pandas>=2.0" "requests>=2.31" \
    && if [ "$INSTALL_GM" = "true" ]; then python -m pip install "gm==3.0.183"; fi

COPY sequoia_x ./sequoia_x
COPY main.py gm_order_once.py gm_sim_strategy.py wechat_login.py start.py ./

CMD ["python", "start.py"]
