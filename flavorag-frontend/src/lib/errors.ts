const STATUS_MESSAGES: Record<number, string> = {
  400: "提交内容有误，请检查后重试。",
  401: "登录状态已失效，请重新登录。",
  403: "当前账号没有执行此操作的权限。",
  404: "要访问的内容不存在，可能已被删除或移动。",
  408: "请求处理超时，请稍后重试。",
  409: "数据已发生变化，请刷新页面后重试。",
  413: "提交内容过大，请缩小文件或问题后重试。",
  422: "提交内容格式不正确，请检查必填项后重试。",
  429: "当前请求较多，请稍后再试。",
  500: "系统处理时遇到异常，问题已自动记录。请稍后重试。",
  502: "上游服务暂时不可用，请稍后重试。",
  503: "服务暂时不可用，请稍后重试。",
  504: "服务响应超时，请稍后重试。",
};

export function friendlyErrorMessage(error: any): string {
  const status = Number(error?.response?.status || 0);
  const payload = error?.response?.data;
  const serverMessage =
    payload?.message ||
    (typeof payload?.detail === "string" && status < 500 ? payload.detail : "");
  const message =
    serverMessage ||
    STATUS_MESSAGES[status] ||
    (error?.name === "AbortError"
      ? "请求已取消。"
      : "网络连接不稳定，请检查网络后重试。");
  const errorId = payload?.errorId;
  return errorId ? `${message}（错误编号：${errorId}）` : message;
}

export function chatErrorMessage(error: Error): string {
  return `这次没有完成。${error.message || STATUS_MESSAGES[500]}`;
}
