// Parsing and fuzzy filtering for GitHub repository rows.
// Rows come from `gh api user/repos` mapped to:
//   { name: "owner/repo", desc: "...", url: "https://...", ssh: "git@...", private: bool }

function parseRepos(raw) {
  try {
    var data = JSON.parse(String(raw || ""))
    if (!Array.isArray(data)) return []
    var out = []
    for (var i = 0; i < data.length; i++) {
      var item = data[i]
      if (!item || !item.name || !item.url) continue
      out.push({
        name: String(item.name),
        desc: String(item.desc || ""),
        url: String(item.url),
        ssh: String(item.ssh || ""),
        priv: !!item.private,
        haystack: (String(item.name) + " " + String(item.desc || "")).toLowerCase()
      })
    }
    return out
  } catch (e) {
    return []
  }
}

function normalizedQuery(query) {
  return String(query || "").trim().toLowerCase()
}

// Every whitespace-separated token must appear somewhere in the row's
// name+description. Rows whose repo name matches score ahead of rows that
// only match in the description, and shorter names beat longer ones so
// "dot" ranks dotfiles above a repo that merely mentions dots.
function filterRepos(repos, query, limit) {
  var values = Array.isArray(repos) ? repos : []
  var needle = normalizedQuery(query)
  var max = limit === undefined || limit === null ? 200 : Number(limit)
  if (isNaN(max)) max = 200
  max = Math.max(0, max)
  if (max === 0) return []

  if (!needle) return values.slice(0, max)

  var tokens = needle.split(/\s+/)
  var scored = []

  for (var i = 0; i < values.length; i++) {
    var item = values[i]
    var nameLower = item.name.toLowerCase()
    var ok = true
    var score = 0
    for (var t = 0; t < tokens.length; t++) {
      var tok = tokens[t]
      var inName = nameLower.indexOf(tok)
      if (inName >= 0) {
        score += 100 - Math.min(inName, 50)
      } else if (item.haystack.indexOf(tok) >= 0) {
        score += 10
      } else {
        ok = false
        break
      }
    }
    if (!ok) continue
    score -= Math.min(nameLower.length, 60) / 10
    scored.push({ item: item, score: score, order: i })
  }

  scored.sort(function(a, b) {
    if (b.score !== a.score) return b.score - a.score
    return a.order - b.order
  })

  var out = []
  for (var j = 0; j < scored.length && out.length < max; j++) out.push(scored[j].item)
  return out
}

if (typeof module !== "undefined") {
  module.exports = {
    parseRepos: parseRepos,
    normalizedQuery: normalizedQuery,
    filterRepos: filterRepos
  }
}
