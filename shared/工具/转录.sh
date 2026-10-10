#!/bin/bash
# 录音转录 —— 把 CLAUDE.md「字幕文件」一节的标准流程封装成一条命令。
#
# 用法：
#   bash shared/工具/转录.sh <音频文件> <输出前缀>
#   例： bash shared/工具/转录.sh ~/Downloads/xxx.mp3 algorithms/字幕/w04
#   → 产出 algorithms/字幕/w04.srt 和 w04.txt
#
# 参数就是 CLAUDE.md 定的那套：large-v3-turbo · 16kHz 单声道 · -l en -t 8
# 全程离线。一节 2 小时的课约 10–20 分钟，丢后台跑（run_in_background）。
#
# 依赖（缺了会给安装提示）：ffmpeg（含 ffprobe）· whisper.cpp 的 whisper-cli · 一个 ggml 模型文件
#   macOS:  brew install ffmpeg whisper-cpp
#   模型:   https://huggingface.co/ggerganov/whisper.cpp 下载 ggml-large-v3-turbo.bin
#           放到 ~/.cache/whisper/ 下即可被自动找到；放别处就设 WHISPER_MODEL=/path/to/model.bin
# 环境变量（都可选）：WHISPER_MODEL 模型路径 · WHISPER_BIN whisper 可执行文件 · WHISPER_THREADS 线程数（默认 8）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

if [[ $# -lt 2 ]]; then
  echo "用法: $0 <音频文件> <输出前缀（相对项目根或绝对路径，不带扩展名）>" >&2
  exit 2
fi
IN="$1"
OUT="$2"
[[ "$OUT" = /* ]] || OUT="$ROOT/$OUT"
[[ -f "$IN" ]] || { echo "❌ 找不到音频: $IN" >&2; exit 1; }

# ---------- 自动探测：ffmpeg / whisper-cli / 模型 ----------
for t in ffmpeg ffprobe; do
  command -v "$t" >/dev/null 2>&1 || { echo "❌ 找不到 $t —— 先装 ffmpeg（macOS: brew install ffmpeg · Debian/Ubuntu: sudo apt install ffmpeg）" >&2; exit 1; }
done

WHISPER="${WHISPER_BIN:-}"
if [[ -z "$WHISPER" ]]; then
  for c in whisper-cli whisper-cpp; do                       # whisper.cpp 新版叫 whisper-cli，有的发行版叫 whisper-cpp
    if command -v "$c" >/dev/null 2>&1; then WHISPER="$(command -v "$c")"; break; fi
  done
fi
if [[ -z "$WHISPER" ]]; then
  for c in "$HOME/whisper.cpp/build/bin/whisper-cli" "$HOME/whisper.cpp/main" "$HOME/.local/bin/whisper-cli" \
           /opt/homebrew/bin/whisper-cli /usr/local/bin/whisper-cli; do
    if [[ -x "$c" ]]; then WHISPER="$c"; break; fi
  done
fi
[[ -n "$WHISPER" ]] || { echo "❌ 找不到 whisper-cli（whisper.cpp）—— macOS: brew install whisper-cpp ；或自己编译后 export WHISPER_BIN=/path/to/whisper-cli" >&2; exit 1; }

MODEL="${WHISPER_MODEL:-}"
if [[ -z "$MODEL" ]]; then
  DIRS=("$HOME/.cache/whisper" "$HOME/.cache/whisper.cpp" "$HOME/.local/share/whisper" "$HOME/.local/share/whisper.cpp" \
        "$HOME/whisper.cpp/models" "$HOME/models/whisper" /opt/homebrew/share/whisper-cpp /usr/local/share/whisper-cpp)
  # 优先 large-v3-turbo，其次逐级退而求其次（识别质量会降，找到哪个用哪个并告诉用户）
  for name in ggml-large-v3-turbo.bin ggml-large-v3.bin ggml-large-v3-turbo-q5_0.bin ggml-medium.en.bin ggml-medium.bin ggml-small.en.bin ggml-small.bin; do
    for d in "${DIRS[@]}"; do
      if [[ -f "$d/$name" ]]; then MODEL="$d/$name"; break 2; fi
    done
  done
  if [[ -n "$MODEL" && "$(basename "$MODEL")" != "ggml-large-v3-turbo.bin" ]]; then
    echo "⚠️ 没找到 ggml-large-v3-turbo.bin，退而用 $(basename "$MODEL")（识别质量会差一点）" >&2
  fi
fi
[[ -n "$MODEL" && -f "$MODEL" ]] || { echo "❌ 找不到 whisper 模型文件。下载 ggml-large-v3-turbo.bin（https://huggingface.co/ggerganov/whisper.cpp）放到 ~/.cache/whisper/，或 export WHISPER_MODEL=/path/to/model.bin" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"

DUR="$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$IN" 2>/dev/null || echo 0)"
MIN="$(awk -v d="${DUR:-0}" 'BEGIN{printf "%.0f", d/60}')"
echo "🎙️ 输入: $IN（${MIN} min）"

TMPDIR_WAV="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_WAV"' EXIT
ffmpeg -y -loglevel error -i "$IN" -ar 16000 -ac 1 -c:a pcm_s16le "$TMPDIR_WAV/a.wav"
"$WHISPER" -m "$MODEL" -f "$TMPDIR_WAV/a.wav" -l en -t "${WHISPER_THREADS:-8}" -osrt -otxt -of "$OUT" >/dev/null 2>&1

echo "✅ ${OUT}.srt"
echo "✅ ${OUT}.txt（$(wc -l < "${OUT}.txt" | tr -d ' ') 行）"
echo "⏭️ 别忘了：① 扫一遍考试提示 → shared/公告.md ② 台账字幕列改 ✅已收"
