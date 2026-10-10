# Ed / Canvas 一键抓取书签（bookmarklet）

**用途**：在 Ed lessons 或 Canvas 页面点一下，把正文抽成 Markdown 下载到 `~/Downloads`，
省掉手动复制粘贴。**零安装**，不用 Chrome 扩展，不用开发者模式。

---

## 怎么装（一次，30 秒）

1. Chrome 里按 **`Cmd + Shift + B`** 显示书签栏（如果没显示）
2. 在书签栏空白处**右键 → 添加网页**
3. **名称**填：`抓Ed`
4. **网址**填：把下面整段代码复制进去（**必须包含开头的 `javascript:`**）
5. 保存

## 怎么用

1. 在 Ed 上打开要抓的那一页（**等内容完全加载出来**，Ed 是单页应用，加载慢）
2. 点书签栏上的 **`抓Ed`**
3. 文件自动下载到 `~/Downloads`，文件名形如 `ed-Worksheet-1-Basics-of-IO.md`
4. 跟我说一声「抓好了」，我去 Downloads 拿并归档

---

## 代码

```javascript
javascript:(function(){var t=document.title.replace(/[\\/:*?"<>|]/g,'-').replace(/\s+/g,'-').slice(0,80);var best=null,max=0;document.querySelectorAll('main,article,section,div').forEach(function(e){var n=e.innerText?e.innerText.length:0;if(n>max&&e.querySelectorAll('div').length<400){max=n;best=e}});var root=best||document.body;var out=['# '+document.title,'','> 来源: '+location.href,'> 抓取时间: '+new Date().toLocaleString(),'','---',''];root.querySelectorAll('h1,h2,h3,h4,p,li,pre,code,td,th').forEach(function(e){var x=(e.innerText||'').trim();if(!x)return;var g=e.tagName.toLowerCase();if(g==='pre'||(g==='code'&&x.indexOf('\n')>-1)){out.push('```java',x,'```','')}else if(g[0]==='h'){out.push('','#'.repeat(+g[1]+1)+' '+x,'')}else if(g==='li'){out.push('- '+x)}else if(g==='td'||g==='th'){out.push('| '+x+' |')}else{out.push(x,'')}});var seen={},lines=out.filter(function(l,i){if(l===''){return true}var k=l+'@@'+i;if(seen[l]&&i>0&&out[i-1]===l)return false;seen[l]=1;return true});var blob=new Blob([lines.join('\n')],{type:'text/markdown'});var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='ed-'+t+'.md';a.click();})()
```

---

## ⚠️ 已知限制

| 问题 | 说明 |
|---|---|
| **Ed 是 React 单页应用** | 内容没渲染完就点 → 抓到空的。**等页面完全出来再点** |
| **代码块语言** | 一律标成 ` ```java `（原来那门课全是 Java）—— 别的语言改代码里那个字符串 |
| **表格** | 抽成一列一行，不是标准 markdown 表格 —— 可读，但不好看 |
| **抓到导航栏/侧边栏** | 启发式挑「文字最多的容器」，偶尔会挑错。抓歪了告诉我，我调选择器 |
| **Canvas** | 同样能用，但 Canvas 的页面结构不同，效果可能更差 |

## 🔒 归档流程（我这边）

收到「抓好了」之后：
1. 去 `~/Downloads` 找最新的 `ed-*.md`
2. 按 `<课程>/Ed/wNN-<类型><编号>-<内容>.md` 重命名归档（练习平台存档目录按需建，在 `shared/配置.json` 的 `extra_core_files` 里登记它的 README）
3. 更新 `<课程>/Ed/README.md` 的状态和存档列
4. 老师在页面上说的考试范围提示 → `shared/公告.md`
