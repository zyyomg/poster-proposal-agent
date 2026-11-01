# Day 1 平台注册清单

> 状态：**待注册（共 3 个）** ｜ 3 个都需本人手机号 / 实名认证，脚本和模型都无法代办
> 成本：DeepSeek 首充可选，百炼按量付费（约 0.2 元/张），GitHub 免费

## 1. DeepSeek 开放平台（文本模型）

| 项 | 内容 |
|---|---|
| 地址 | https://platform.deepseek.com |
| 注册 | 手机号 + 验证码，无需实名 |
| 要拿的东西 | API Key（`sk-` 开头），保存在本地，**不进仓库、不进截图** |
| 落位 | `.env` 里的 `DEEPSEEK_API_KEY` |
| 费用 | 有免费额度；充值建议先充 10 元（总预算上限 20 元） |
| 核对项 | 控制台「模型」页确认可用模型 ID。章程写的是 `deepseek-flash`，若控制台实际叫别的名字，**以控制台为准并回填到本文档**，这不是换技术栈 |

## 2. 阿里云百炼（图像模型 `wan2.6-image`）

| 项 | 内容 |
|---|---|
| 地址 | https://bailian.console.aliyun.com |
| 注册 | 阿里云账号 + **实名认证**（个人支付宝人脸最快），不实名不能开通百炼 |
| 要拿的东西 | 百炼 API Key（`sk-` 开头），不是阿里云 AccessKey |
| 落位 | `.env` 里的 `DASHSCOPE_API_KEY` |
| 费用 | 图像 0.2 元/张（已核实，见下）；Day 5 只跑 5 条演示 brief，总量 ≤40 张 = ≤8 元 |
| 注意事项 | 开通时选「按量付费」，不要买任何套餐包 |

### 图像模型选型：已定，不需要挑

| 项 | 值 | 来源 |
|---|---|---|
| 模型 | `wan2.6-image` | 章程 §2.1 选型（不中途更换） |
| 单价（华北2·北京） | **0.2 元/张** | [官方模型页](https://help.aliyun.com/zh/model-studio/wan2-6-image)（新加坡 0.220177 元/张） |
| 限流（北京） | 300 RPM | 同上，本项目用不到这个量级 |
| 预算测算 | 全量上限 40 张 = **8 元**；演示 5 条 × 2 张 = 10 张 = **2 元** | 章程 §2.2 红线 20 元 |

结论：章程写的「约 0.2 元/张」与官方价一致，**没有换模型的理由**。切换同类文生图模型省下的钱（每张几分钱）远小于重跑评测、重写文档、重录屏的成本，且违反章程禁止事项第 5 条。

**同价对照（2026-10-30 控制台实拍，华北2·北京）**：`wan2.6-t2i`（Wan-T2I）也是 **0.2 元/张**，但只吃文本输入、RPM 60；`wan2.6-image`（Wan-Image）**同价**，支持文本+图片输入、RPM 300。**同价就取能力更强的那个**，所以选 `wan2.6-image` 不是偏好问题，是白拿。

**模型广场不需要逐个「开通」**：百炼开通一次即可，建好 API Key 直接调用。模型列表里那个「+」是「模型对比」按钮，不是开通入口——点它只会多一个对比栏，别在那儿卡住。若调用报「未开通 / 无权限」，再回模型广场点该模型的「开通」，开通本身免费，按调用计费。

### 密钥类型坑（2026-10-30 实遇，**结论已修正**）

> ⚠️ 这一节之前写错过，留在这里是为了记住「凭前缀下结论」这个错误本身。

**错误结论**：`sk-ws-` = Coding Plan，只能文本、不能生图。
**实测结论**：本账号的 `sk-ws-` key **能调文本也能生图**（qwen-plus 200、wan2.6-image 出图成功）。

**真正的 401 原因**：key 本身失效——不是套餐问题。可能是在控制台点过「重置」、创建时复制被截断，或账号侧回收。换成新 key 后立刻全通。

**判断 key 是否可用的唯一方法：真实模型调用。**

| 试探方式 | 结果 | 能不能当证据 |
|---|---|---|
| `GET /models` | 200 但 0 个模型 | ❌ 未授权的 key 也回 200 |
| 假模型名 `__nope__` 打 chat | 有时 404 | ❌ 服务器可能鉴权前就返回，误判为「AUTH OK」 |
| `qwen-plus` + `max_tokens=1` | 200 / 401 | ✅ 约 0.0001 元，最便宜的确定性判据 |
| 真实出图一张 | 200 + 图片 | ✅ 0.2 元，最终判据 |

**同一条链路上的两个次要事实**：
- 业务空间专属域名 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com` 可用；旧域名 `dashscope.aliyuncs.com` 也仍然可用（官方公告：2026-09-30 起旧域名进入维护状态，不再支持新特性）。本项目先按旧域名写，Day 5 前切到业务空间域名。
- 控制台显示「余额不足」：`wan2.6-image` 没有免费额度（免费额度页只有 `wan2.7-image` / `wan2.7-image-pro` 各 50 张），所以出图按 0.2 元/张实扣。

### 图像链路实测（2026-10-30，2 张，0.40 元）

用 b01「黑场光束」的真实提示词出图，720×1280，成功。**但发现一个必须修的问题**：

> 提示词里写了「画面中不出现任何文字」，模型仍在画面左侧生成了乱码英文（`Stnuk Mutcin` / `CLAFRESSIN`）。

**处置（Day 5 要落到代码里）**：
1. `parameters.negative_prompt` 必须加：「文字、字母、水印、logo、签名、乱码」；
2. 海报上的所有文字**一律后期加**（Figma/PS/代码排版），绝不让图像模型画字；
3. 出图后做一次「有没有多余字符」的自动检查，发现的图直接重跑或裁掉。

**同类坑：密钥写错文件。** 第一次填的时候写进了 `.env.example`（这个文件会进公开仓库），不是 `.env`。已修正，并把 `.gitignore` 收紧为 `.env.*` + `*.bak`，只放行 `.env.example`。


## 3. GitHub（公开仓库）

| 项 | 内容 |
|---|---|
| 地址 | https://github.com |
| 用途 | 公开仓库，可对外分享 |
| 前置检查 | 推送前跑一遍 `docs/day1_review.md` 里的「脱敏自查」：无客户名、无公司内部资料、无同事姓名、无 API Key |
| 落位 | `GITHUB_TOKEN` 只在本机用，不写进仓库 |

## 注册完成后自检（3 条命令）

```bash
# 1. 文本模型能不能通
python -c "from openai import OpenAI; import os; c=OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url='https://api.deepseek.com'); print(c.models.list().data[0].id)"

# 2. 图像模型能不能通（Day 5 再用，Day 1 只确认 key 有效）
python -c "import os,requests; print(requests.get('https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis', headers={'Authorization':'Bearer '+os.environ['DASHSCOPE_API_KEY']}).status_code)"

# 3. 密钥没有被打进 git
git status --porcelain | findstr /i ".env"
```

> ⚠️ 上面第 2 条只是探活，真正出图在 Day 5；Day 1 的验收**不需要**图像模型可用。

## 本会话当前实测（2026-10-30 20:52 更新）

| 平台 | 状态 |
|---|---|
| 阿里云百炼 | **账号已注册、实名认证已完成**（控制台「上云引导」2/4：实名认证 ✅、账号安全设置 ✅）；API Key 尚未创建 |
| DeepSeek | 未注册，`DEEPSEEK_API_KEY` 未设置 |
| GitHub | 未注册，`GITHUB_TOKEN` 未设置 |
| Python 依赖 | `openai` / `streamlit` / `pandas` / `dashscope` 均未安装（Day 3 再装） |

### 百炼这一步的坑（已经在 RAM 页面卡过一次）

在 RAM 控制台（`ram.console.aliyun.com/profile/access-keys`）点「创建 AccessKey」会弹「不建议使用云账号 AccessKey」。**这条路是错的**：

- RAM AccessKey = AccessKeyId + AccessKeySecret 一对，用来调阿里云 OpenAPI，**百炼模型用不到**；
- 百炼 API Key = 单个 `sk-` 字符串，在百炼控制台的 **API-KEY** 页面创建；
- 官方原话：「百炼 API Key 不是密钥对，创建后只会得到一个字符串，不存在 AccessKeyId 与 AccessKeySecret」。

正确入口：<https://bailian.console.aliyun.com> → API-KEY → 创建我的 API-KEY → 归属账号「主账号」/ 业务空间「默认」/ 权限「全部」。

**地域不能混用**：北京地域的 Key 只能配北京域名 `https://dashscope.aliyuncs.com/compatible-mode/v1`；换地域要重新建 Key。

### 探活（零成本，不产生计费调用）

```powershell
python tools/probe_keys.py
```

它会脱敏打印每个 key（前 6 位 + 长度），用来发现「少复制一段 / 带了空格 / 填成了 RAM AccessKey」这三类最常见的手误。

