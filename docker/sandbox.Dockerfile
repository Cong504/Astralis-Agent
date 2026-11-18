# sandbox.Dockerfile — W2 沙箱基础镜像（ADR-0001 接口 / SANDBOX_RULES.md §2/§5/§6）
# 基线：Python 3.11 + Bash + 标准库，0 额外依赖，极简。
# 硬约束：运行期禁止包管理（人类确认 2026-11-18）——构建期删除 pip/ensurepip，
#         运行期拦截由 src/sandbox/executor.py 的 §3.5 规则承担（双层防御）。
FROM python:3.11-slim

ARG AGENT_UID=10001

# 非 root 专用用户（SANDBOX_RULES.md §2：运行用户非 root）
RUN groupadd -g ${AGENT_UID} agent \
 && useradd -u ${AGENT_UID} -g agent -m -s /bin/bash agent

# 运行期禁止包管理：移除 pip 与 ensurepip，防止容器内自行装包。
# 注意：这是「加固」而非唯一防线；真正的拦截在执行层 check_command()（DENIED_BY_RULE §3.5）。
RUN rm -rf /usr/local/lib/python3.11/ensurepip \
 && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.11

# 目录约定（SANDBOX_RULES.md §5）
#   /workspace         任务输入只读挂载点（由 docker run -v ro 挂载，镜像内只建空目录）
#   /tmp/agent-scratch 可写临时目录（由执行器用 tmpfs 挂载，≤500MB，任务结束即销毁）
RUN mkdir -p /workspace /tmp/agent-scratch \
 && chown -R ${AGENT_UID}:${AGENT_UID} /tmp/agent-scratch \
 && chmod 0555 /workspace

USER ${AGENT_UID}:${AGENT_UID}
WORKDIR /workspace

# 最小可用入口：保持容器存活，由执行器 docker exec 注入命令
CMD ["/bin/bash", "-c", "while true; do sleep 3600; done"]
