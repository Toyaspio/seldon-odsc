import type { NewsAnalysis, Report, Source } from '../../types/api'

type Insight = NewsAnalysis['insights'][number]
const assessments = {
  positive: 'Constructive', neutral: 'Neutral', cautious: 'Cautious', mixed: 'Mixed signals',
  insufficient: 'Insufficient evidence',
}
const states = {
  completed: 'Analysis available', partial: 'Partial analysis', search_failed: 'News search failed',
  no_results: 'No usable news results', not_executed: 'News research was not run',
}
const generation: Record<string, string> = {
  generation_truncated: 'Model output truncated',
  generation_failed: 'Model generation failed',
  budget_unavailable: 'Generation budget unavailable',
  news_collected: 'News collected; analysis unavailable',
  news_analysis_completed: 'News analysis available; article pending',
}
const attribution: Record<string, string> = {
  management_statement: 'Company statement', analyst_view: 'Attributed analyst view',
  reported_event: 'Reported event', unclassified: 'Search lead',
}
const topics: Record<string, string> = {
  operations_strategy: 'Operations and strategy', earnings: 'Earnings', products_technology: 'Products and technology',
  industry: 'Industry', competition: 'Competition', analyst_views: 'Analyst views', macro: 'Macro',
  policy_regulation: 'Policy and regulation', geopolitics: 'Geopolitics', other: 'Other',
}

function sourceLink(source: Source) {
  if (!/^https:\/\//i.test(source.url)) return <span>{source.title}</span>
  return <a className="text-info-400 underline" href={source.url} target="_blank" rel="noreferrer">
    {source.title}
  </a>
}

function References({ ids, sources }: { ids: string[]; sources: Map<string, Source> }) {
  return <ul aria-label="Evidence references" className="space-y-1 text-xs text-fog-400">
    {ids.map((id) => {
      const source = sources.get(id)
      if (!source) return null
      const kind = ['sec_xbrl', 'sec_statement', 'esef_xbrl'].includes(source.source_type)
        ? 'Financial evidence' : 'News source'
      return <li key={id}>{kind} · {source.publisher} · {sourceLink(source)}</li>
    })}
  </ul>
}

function Insights({ rows, sources }: { rows: Insight[]; sources: Map<string, Source> }) {
  return <div className="space-y-4">{rows.map((item, index) => (
    <div key={index} className="space-y-1">
      <p className="text-sm leading-relaxed text-fog-200">{item.text}</p>
      <References ids={item.evidence_ids} sources={sources} />
    </div>
  ))}</div>
}

export function NewsIntelligence({ report }: { report: Report }) {
  const news = report.news_analysis
  const technical = news?.generation_status ? generation[news.generation_status] : undefined
  const sources = new Map(report.sources.map((source) => [source.source_id, source]))
  const positives = news?.insights.filter((item) => item.kind === 'positive') ?? []
  const negatives = news?.insights.filter((item) => item.kind === 'negative') ?? []
  const narrative = news?.insights.filter((item) => item.kind === 'synthesis') ?? []
  const sections = [
    { kind: 'cross_news', title: 'How the developments connect' },
    { kind: 'business', title: 'Operating implications' },
    { kind: 'financial', title: 'Financial implications' },
    { kind: 'market_expectations', title: 'Market expectations' },
  ]
  return <section aria-label="News and market intelligence" className="panel space-y-5 border-l-2 border-l-signal-500 p-5">
    <header className="space-y-1">
      <h2 className="text-lg font-semibold">News &amp; Market Intelligence</h2>
      <p className="text-xs text-fog-400">{news ? states[news.status] + ' · As of ' + news.as_of : 'No news research was recorded for this report.'}</p>
      {news && <p className="text-xl font-semibold text-fog-100">
        {technical ?? assessments[news.overall_assessment]}{!technical && news.insights.length > 0 &&
          <span className="ml-3 text-sm font-normal">{news.confidence} confidence</span>}
      </p>}
    </header>
    {news?.failed_stage === 'article' && <p className="text-sm text-fog-300">
      Relevant news was collected and analyzed. The synthesis article is incomplete.
    </p>}
    {narrative.length > 0 && <section className="space-y-3">
      <h3 className="font-semibold">Agent assessment</h3>
      <p className="text-xs text-fog-400">Agent interpretation based on the cited sources.</p>
      <Insights rows={narrative} sources={sources} />
    </section>}
    {!!news?.news_items.length && <section className="space-y-3">
      <h3 className="font-semibold">Important developments</h3>
      <div className="grid items-start gap-3 lg:grid-cols-2">
        {news.news_items.map((item) => {
          const source = sources.get(item.source_id)
          if (!source) return null
          const implications = news.insights.filter((insight) => insight.kind !== 'synthesis'
            && insight.kind !== 'cross_news' && insight.evidence_ids.includes(item.source_id))
          return <details key={item.source_id} className="rounded-lg border border-ink-700 bg-ink-900 p-3">
            <summary className="cursor-pointer space-y-1">
              <span className="block text-xs text-fog-400">{attribution[item.attribution] ?? 'Search lead'} · {topics[item.category] ?? item.category}</span>
              <strong className="block text-sm text-fog-100">{source.title}</strong>
              <span className="block text-xs text-fog-400">{source.publisher} · Published {item.publication_date ?? 'date unavailable'}
                {item.publication_basis === 'search_metadata' && ' (search metadata)'}</span>
              {item.event_date && <span className="block text-xs text-fog-400">Reported event date {item.event_date}</span>}
            </summary>
            <div className="mt-3 space-y-2 border-t border-ink-700 pt-3 text-sm">
              <p>{item.summary}</p>
              {item.excerpt && <blockquote className="border-l border-signal-600 pl-2 text-xs text-fog-300">{item.excerpt}</blockquote>}
              <p className="text-xs text-fog-400">{item.publisher_excerpt_matched ? 'Excerpt matched to the original publisher page.'
                : 'Search excerpt. Original page text was not independently matched.'}</p>
              <p className="text-xs">{sourceLink(source)}</p>
              {implications.length > 0 && <div className="space-y-2 pt-2">
                <h4 className="font-semibold">Agent interpretation</h4>
                <Insights rows={implications} sources={sources} />
              </div>}
              {!!item.related_source_ids.length && <div className="text-xs">
                <p>Related reporting</p><References ids={item.related_source_ids} sources={sources} />
              </div>}
            </div>
          </details>
        })}
      </div>
    </section>}
    {(positives.length > 0 || negatives.length > 0) && <div className="grid items-start gap-3 lg:grid-cols-2">
      {positives.length > 0 && <section className="space-y-2 rounded-lg border border-ink-700 p-3">
        <h3 className="font-semibold">Potential upside</h3><Insights rows={positives} sources={sources} />
      </section>}
      {negatives.length > 0 && <section className="space-y-2 rounded-lg border border-ink-700 p-3">
        <h3 className="font-semibold">Potential downside</h3><Insights rows={negatives} sources={sources} />
      </section>}
    </div>}
    {sections.map(({ kind, title }) => {
      const rows = news?.insights.filter((item) => item.kind === kind) ?? []
      return rows.length ? <section key={kind} className="space-y-2">
        <h3 className="font-semibold">{title}</h3><Insights rows={rows} sources={sources} />
      </section> : null
    })}
    {!!news?.limitations.length && <ul className="list-disc space-y-1 pl-5 text-xs text-fog-400">
      {news.limitations.map((note, index) => <li key={index}>{note}</li>)}
    </ul>}
  </section>
}
