import type { InvestmentAnalysis, InvestmentNarrative, Report } from '../types/api'

type Section = InvestmentNarrative['sections'][number]
type Point = InvestmentAnalysis['points'][number]

export const evidenceQualification = 'This is a conditional interpretation of the available material.'
export const incompleteEvidence = 'Some supporting information remains unconfirmed. This view is based on the evidence currently available.'

const editorial = /(?:^|[.!?;]\s+)(?:please\s+|kindly\s+)?(?:flag|retain|drop|cite|label|rewrite|rephrase|revise|replace|remove|change|present|qualify|ensure|verify|check|add|delete|clarify|reconcile|perform)\b|\b(?:must|should|needs? to)\s+(?:be\s+)?(?:replace|remove|cite|label|rewrite|revise|check|change|qualify)\w*/i
const workflow = /\b(?:verdict|provisional|review\w*|batch|item_id|supported_direction|schema|retry)\b|_[a-z]+/i

function sentence(text: string): string {
  const value = text.trim().replace(/;\s*/g, '. ').replace(/^[a-z]/, char => char.toUpperCase())
  return /[.!?]$/.test(value) ? value : value + '.'
}

function readableStatement(text: string): boolean {
  return !editorial.test(text) && !workflow.test(text) &&
    /^(?:A |An |If |Unless |Without |While |Given |The |This |These |Those |No |Only |Both |Guidance |Revenue |Growth |Cash |Facts |Management |Counterargument |Verified |Reported |Available |Evidence |Market |Current |Financial |Investment |My |I |[A-Z]{2,}\b|[0-9])/u.test(text) &&
    /\b(?:is|are|was|were|has|have|would|could|may|might|remain\w*|assum\w*|depend\w*|support\w*|unverified|match\w*|lack\w*|confirm\w*|not shown|rel\w*|come\w*|cover\w*|show\w*|offer\w*|exclude\w*|cannot)\b/i.test(text)
}

/** Presentation only. Preserve the original note, verdict, paragraph and sources. */
export function investorNote(revision?: string | null, reason?: string | null): string {
  const text = revision?.trim() || ''
  if (text && readableStatement(text)) return text

  const correction = text.match(/^Change ['“](.+?)['”] to ['“](.+?)['”] based on (.+?)\.?$/i)
  if (correction) {
    return `The passage’s statement “${correction[1]}” conflicts with the cited inputs. ` +
      `Using ${correction[3].replace(/\.$/, '')} gives ${correction[2]}.`
  }
  const commentary = text.match(/^Flag (.+?) as unverified commentary rather than sourced guidance\.?$/i)
  if (commentary) return sentence(`${commentary[1]} comes from unverified commentary and has not been established as company guidance`)

  const assumptions = text.match(/^Label (.+?) as (.+?)(?:, not sourced facts)?\.?$/i)
  if (assumptions) return sentence(`This scenario treats the ${assumptions[1].replace(/^the /i, '')} as ${assumptions[2]}`)

  const attribution = text.match(/^Retain .+? but keep attribution to commentary sources only; (.+)$/i)
  if (attribution) return `This risk is based on commentary. ${sentence(attribution[1]).replace(/^no /, 'No ')}`

  const media = text.match(/^Present (.+?) explicitly as unverified media claims(?:,.*)?$/i)
  if (media) return sentence(`The reported figures (${media[1].replace(/^the /i, '')}) come from unverified media claims`)

  const assumption = text.match(/^Drop or evidence (.+?); alternatively qualify it as an unverified assumption\.?$/i)
  if (assumption) return sentence(`${assumption[1]} remains an unverified assumption for this outlook`)

  const missingSource = text.match(/^Cite or drop (.+?); note (.+)$/i)
  if (missingSource && readableStatement(missingSource[2])) {
    return `The cited evidence does not establish ${missingSource[1]}. ${sentence(missingSource[2])}`
  }
  const limitedSource = text.match(/^Cite only (.+?) as evidenced; note (.+)$/i)
  if (limitedSource) {
    return `The cited material establishes ${limitedSource[1]}. ` +
      sentence(`The scenario also refers to ${limitedSource[2].replace(/ context\.?$/, '')}`)
  }

  // A reason can describe the missing evidence even when its paired revision is an instruction.
  const gap = reason?.match(/\bbut (.+?) is not (supported by|among) (?:the )?cited evidence\.?$/i)
  if (gap) return sentence(`The cited evidence does not establish ${/^['“]/.test(gap[1]) ? 'that ' : ''}${gap[1].replace(/^['“]|['”]$/g, '')}`)
  if (reason && readableStatement(reason)) return sentence(reason)
  return evidenceQualification
}

export function highlights(report: Report) {
  const points = report.investment_analysis?.points ?? []
  return {
    primary: points.find(point => point.topic === 'thesis') ??
      points.find(point => ['base_case', 'outlook', 'bull_case', 'bear_case'].includes(point.topic)) ?? points[0],
    risk: points.find(point => ['counterargument', 'opposing_case', 'risk'].includes(point.topic)),
    trigger: points.find(point => point.topic === 'invalidation'),
  }
}

export function sectionFor(report: Report, point?: Point): Section | undefined {
  return point && report.investment_narrative?.sections.find(section =>
    section.topic === point.topic && section.text === point.text)
}

export function sectionQualification(section?: Section): string | null {
  if (!section) return null
  if (['partially_supported', 'needs_qualification'].includes(section.review_verdict)) {
    return investorNote(section.suggested_revision, section.review_reason)
  }
  return section.review_verdict === 'pending' ? incompleteEvidence : null
}

export function headlineQualifications(report: Report): string[] {
  const { primary, risk, trigger } = highlights(report)
  return [...new Set([primary, risk, trigger].map(point =>
    sectionQualification(sectionFor(report, point))).filter((note): note is string => Boolean(note)))]
}

export function headlineExplanation(report: Report): string | null {
  const reason = report.investment_analysis?.observation_reason?.trim()
  const notes = headlineQualifications(report)
  const { primary } = highlights(report)
  const section = sectionFor(report, primary)
  // summarize() may copy the very same qualification into observation_reason.
  if (reason && reason !== section?.review_reason?.trim() && reason !== section?.suggested_revision?.trim()) {
    const explanation = investorNote(reason, reason)
    if (!notes.includes(explanation)) return explanation
  }
  return !notes.length && report.investment_review?.opinion === 'provisional' ? incompleteEvidence : null
}
