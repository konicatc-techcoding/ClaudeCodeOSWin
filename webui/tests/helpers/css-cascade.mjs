// 極小型 CSS cascade 解析器(測試用)——用來**實際驗證特異性/優先權**,
// 而不是只讀原始碼猜「應該會生效」。
//
// 存在理由:2026-07-24 工作樹 pill 的 color 沒生效,根因是
// `.update-facts span`(0,1,1)蓋過 `.update-tree-clean`(0,1,0)。
// 這種 bug 靠肉眼看 CSS 很容易漏,故以程式計算 cascade 結果鎖定。
//
// 用 postcss 解析(vite 既有依賴,零新增)。支援本專案樣式實際用到的
// 選擇器形態:類別/元素 compound、後代( )與子代(>)組合子、多重選擇器。
// 不支援 +/~ 與 pseudo(本檔涉及的規則沒用到,遇到一律視為不匹配)。
import postcss from "postcss";

function parseCompound(text) {
  const tag = (text.match(/^[a-zA-Z][\w-]*/) || [null])[0];
  const classes = [...text.matchAll(/\.([\w-]+)/g)].map((m) => m[1]);
  const ids = [...text.matchAll(/#([\w-]+)/g)].map((m) => m[1]);
  const pseudos = [...text.matchAll(/:{1,2}([\w-]+)/g)].map((m) => m[1]);
  const attrs = [...text.matchAll(/\[[^\]]+\]/g)].map((m) => m[0]);
  return { tag, classes, ids, pseudos, attrs };
}

export function parseSelector(sel) {
  const parts = [];
  let buf = "";
  let combinator = " ";
  const flush = () => {
    if (buf.trim()) {
      parts.push({ combinator, compound: parseCompound(buf.trim()) });
      buf = "";
    }
  };
  for (const ch of sel.trim()) {
    if (ch === ">" || ch === "+" || ch === "~") {
      flush();
      combinator = ch;
    } else if (/\s/.test(ch)) {
      if (buf.trim()) {
        flush();
        combinator = " ";
      }
    } else {
      buf += ch;
    }
  }
  flush();
  return parts;
}

function compoundMatches(compound, node) {
  if (compound.ids.length || compound.pseudos.length || compound.attrs.length) return false;
  if (compound.tag && compound.tag !== node.tag) return false;
  return compound.classes.every((c) => (node.classes || []).includes(c));
}

// chain = [root, …, target](每個 {tag, classes})
function matchFrom(parts, pi, chain, ci) {
  if (ci < 0 || !compoundMatches(parts[pi].compound, chain[ci])) return false;
  if (pi === 0) return true;
  const comb = parts[pi].combinator;
  if (comb === ">") return matchFrom(parts, pi - 1, chain, ci - 1);
  if (comb === " ") {
    for (let k = ci - 1; k >= 0; k--) {
      if (matchFrom(parts, pi - 1, chain, k)) return true;
    }
    return false;
  }
  return false; // + / ~ 不支援
}

export function selectorMatches(sel, chain) {
  const parts = parseSelector(sel);
  if (!parts.length) return false;
  return matchFrom(parts, parts.length - 1, chain, chain.length - 1);
}

// 回傳 [ids, classes, types](CSS 特異性三元組)
export function specificity(sel) {
  let ids = 0;
  let classes = 0;
  let types = 0;
  for (const { compound } of parseSelector(sel)) {
    ids += compound.ids.length;
    classes += compound.classes.length + compound.pseudos.length + compound.attrs.length;
    if (compound.tag) types += 1;
  }
  return [ids, classes, types];
}

function cmpRank(a, b) {
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}

/**
 * 計算某個元素(以 chain 描述)在給定樣式表下的最終宣告值。
 * 回傳 { prop: value }(已解析一層 var(--x) → :root 的定義值)。
 */
export function computeStyle(css, chain) {
  const root = postcss.parse(css);

  // 先收 :root 的自訂屬性,供 var() 解析
  const vars = new Map();
  root.walkRules((rule) => {
    if (!rule.selectors.includes(":root")) return;
    rule.walkDecls((decl) => {
      if (decl.prop.startsWith("--")) vars.set(decl.prop.trim(), decl.value.trim());
    });
  });

  const winners = new Map(); // prop -> { rank, value }
  root.walkRules((rule) => {
    // 忽略 at-rule(@media 等)內的規則——不模擬 viewport 條件
    if (rule.parent && rule.parent.type === "atrule") return;
    const matched = rule.selectors.filter((sel) => selectorMatches(sel, chain));
    if (!matched.length) return;
    // 同一 rule 取其中特異性最高的匹配選擇器
    const best = matched
      .map((sel) => specificity(sel))
      .sort(cmpRank)
      .pop();
    rule.walkDecls((decl) => {
      const rank = [decl.important ? 1 : 0, ...best];
      const prev = winners.get(decl.prop);
      // 文件順序較後者在同分時勝出 → 用 >=
      if (!prev || cmpRank(rank, prev.rank) >= 0) {
        winners.set(decl.prop, { rank, value: decl.value.trim() });
      }
    });
  });

  const out = {};
  for (const [prop, { value }] of winners) {
    const m = value.match(/^var\((--[\w-]+)\)$/);
    out[prop] = m && vars.has(m[1]) ? vars.get(m[1]) : value;
  }
  return out;
}
