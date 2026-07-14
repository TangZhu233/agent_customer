"""
FastAPI 入口：启动 HTTP 服务，提供 /chat 对话接口 + 中文聊天页面。
启动命令：uvicorn app.main:app --reload
"""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from app.models import ChatRequest, ChatResponse
from app.agent import chat

app = FastAPI()


# ==================== 聊天页面（中文界面）====================
CHAT_PAGE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>智能客服</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: "Microsoft YaHei", "苹方", sans-serif; background: #f0f2f5; height: 100vh; display: flex; justify-content: center; align-items: center; }
        .chatbox { width: 800px; max-width: 95vw; height: 90vh; background: #fff; border-radius: 16px; box-shadow: 0 4px 24px rgba(0,0,0,.08); display: flex; flex-direction: column; overflow: hidden; }
        .header { background: linear-gradient(135deg, #4f6ef7, #6c8cff); color: #fff; padding: 18px 24px; font-size: 18px; font-weight: bold; text-align: center; }
        .header span { font-size: 13px; opacity: .75; font-weight: normal; display: block; margin-top: 4px; }
        .messages { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
        .msg { max-width: 80%; padding: 12px 16px; border-radius: 12px; line-height: 1.6; word-break: break-word; font-size: 14px; }
        .msg.user { align-self: flex-end; background: #4f6ef7; color: #fff; border-bottom-right-radius: 4px; }
        .msg.ai { align-self: flex-start; background: #f0f2f5; color: #333; border-bottom-left-radius: 4px; }
        .msg.ai pre { background: #e0e2e8; padding: 8px 12px; border-radius: 8px; overflow-x: auto; font-size: 13px; margin: 6px 0; }
        .msg.ai code { background: #e0e2e8; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
        .input-area { display: flex; padding: 14px 20px; border-top: 1px solid #e8e8e8; gap: 10px; background: #fafafa; }
        .input-area input { flex: 1; padding: 12px 16px; border: 1px solid #ddd; border-radius: 24px; font-size: 14px; outline: none; transition: border .2s; }
        .input-area input:focus { border-color: #4f6ef7; }
        .input-area button { padding: 10px 24px; background: #4f6ef7; color: #fff; border: none; border-radius: 24px; font-size: 14px; cursor: pointer; transition: opacity .2s; white-space: nowrap; }
        .input-area button:hover { opacity: .85; }
        .input-area button:disabled { opacity: .5; cursor: not-allowed; }
        .loading { display: flex; gap: 4px; padding: 12px 16px; align-self: flex-start; }
        .loading span { width: 8px; height: 8px; background: #aaa; border-radius: 50%; animation: bounce 1.4s infinite ease-in-out both; }
        .loading span:nth-child(1) { animation-delay: -.32s; }
        .loading span:nth-child(2) { animation-delay: -.16s; }
        @keyframes bounce { 0%,80%,100% { transform: scale(0); } 40% { transform: scale(1); } }
        .tips { font-size: 12px; color: #999; text-align: center; margin-bottom: -4px; }
    </style>
</head>
<body>
<div class="chatbox">
    <div class="header">
        智能客服助手
        <span>可以查询用户、订单、物流信息</span>
    </div>
    <div class="messages" id="msgs">
        <div class="msg ai">你好！我是智能客服助手，可以帮你：<br><br>
        📱 <b>查用户</b> — 告诉我手机号<br>
        📦 <b>查订单</b> — 帮你查订单状态<br>
        🚚 <b>查物流</b> — 追踪快递进度<br><br>
        直接打字问我吧！</div>
    </div>
    <div class="tips" id="tips"></div>
    <div class="input-area">
        <input id="inp" placeholder="输入你的问题，例如：帮我查手机号13800138001的用户..." autofocus>
        <button id="btn" onclick="send()">发送</button>
    </div>
</div>
<script>
const msgs = document.getElementById('msgs');
const inp = document.getElementById('inp');
const btn = document.getElementById('btn');
const tips = document.getElementById('tips');

function addMsg(text, role) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.textContent = text;
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
}

function showLoading() {
    const div = document.createElement('div');
    div.className = 'loading';
    div.id = 'loader';
    div.innerHTML = '<span></span><span></span><span></span>';
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
}

function hideLoading() {
    const loader = document.getElementById('loader');
    if (loader) loader.remove();
}

async function send() {
    const text = inp.value.trim();
    if (!text) return;
    addMsg(text, 'user');
    inp.value = '';
    btn.disabled = true;
    showLoading();
    try {
        const res = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text })
        });
        const data = await res.json();
        hideLoading();
        addMsg(data.reply || '(未收到回复)', 'ai');
    } catch (e) {
        hideLoading();
        addMsg('网络出错了，请确认服务已启动', 'ai');
    }
    btn.disabled = false;
    inp.focus();
}

inp.addEventListener('keydown', e => { if (e.key === 'Enter') send(); });
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def chat_page():
    """中文聊天页面"""
    return CHAT_PAGE


@app.get("/health")
def health():
    """健康检查"""
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat_api(req: ChatRequest):
    """对话接口"""
    reply = await chat(req.message)
    return ChatResponse(reply=reply)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
