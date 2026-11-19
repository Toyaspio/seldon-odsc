import test from 'node:test'
import assert from 'node:assert/strict'
import { investorNote, headlineExplanation, headlineQualifications, sectionQualification, evidenceQualification, incompleteEvidence } from '../src/lib/investmentPresentation.ts'

test('commentary numbers remain attributed and retain their value', () => {
  assert.equal(investorNote('Flag the ~$130B next-quarter revenue figure as unverified commentary rather than sourced guidance.'),
    'The ~$130B next-quarter revenue figure comes from unverified commentary and has not been established as company guidance.')
})

test('scenario assumptions are a complete reader statement', () => {
  assert.equal(investorNote('Label deceleration range and share-loss assumption as explicit scenario assumptions, not sourced facts.'),
    'This scenario treats the deceleration range and share-loss assumption as explicit scenario assumptions.')
})

test('source restrictions preserve their negation and scope', () => {
  assert.equal(investorNote('Retain the risk but keep attribution to commentary sources only; no SEC data corroborates software maturity.'),
    'This risk is based on commentary. No SEC data corroborates software maturity.')
})

test('a missing partnership basis stays an assumption', () => {
  assert.equal(investorNote('Drop or evidence the AMD AI-partnership claim; alternatively qualify it as an unverified assumption.'),
    'The AMD AI-partnership claim remains an unverified assumption for this outlook.')
})

test('numeric conflicts retain both claims and their calculation inputs', () => {
  const text = investorNote("Change 'AMD converts under 2%' to 'roughly 4%' based on $554M FCF against ~$13.4B quarterly revenue.")
  for (const value of ['under 2%', 'roughly 4%', '$554M', '$13.4B', 'conflicts']) assert.ok(text.includes(value))
  assert.ok(!text.includes('unconfirmed'))
})

test('already complete natural qualification is unchanged', () => {
  const text = 'This view is conditional on continued cash conversion.'
  assert.equal(investorNote(text), text)
  const scenario = 'This scenario assumes no change in cash conversion.'
  assert.equal(investorNote(scenario), scenario)
  for (const condition of ['A sustained demand slowdown would weaken this view.',
    'If cash collections weaken, this view would need reassessment.']) {
    assert.equal(investorNote(condition), condition)
  }
})

test('an evidence gap can be extracted from an editing suggestion’s reason', () => {
  assert.equal(investorNote('Cite or drop the claim.', "Guidance is cited, but NVIDIA's cash-generation claim is not among the cited evidence."),
    "The cited evidence does not establish NVIDIA's cash-generation claim.")
})

test('citation instructions retain the cash scope and missing financial figure', () => {
  const text = investorNote('Cite or drop the ~$13B AMD quarterly revenue figure; note NVDA cash ($22.4B) excludes investments/securities.')
  assert.ok(text.includes('does not establish the ~$13B AMD quarterly revenue figure'))
  assert.ok(text.includes('NVDA cash ($22.4B) excludes investments/securities.'))
})

test('limited partnership evidence and share context both remain readable', () => {
  assert.equal(investorNote('Cite only the Anthropic partnership as evidenced; note the 5-7% current accelerator share context.'),
    'The cited material establishes the Anthropic partnership. The scenario also refers to the 5-7% current accelerator share.')
})

for (const instruction of ['Reconcile unexplained adjustments.', 'Kindly recalibrate the scenario.',
  'Perform a second pass because the figure is unsupported.', 'The analyst should replace this paragraph.',
  'Ensure the figure is cited.', 'REVIEW_BLOCKED: rerun batch 4']) {
  test('unknown editorial text uses a restrained fallback — ' + instruction, () => {
    assert.equal(investorNote(instruction), evidenceQualification)
  })
}

function report() {
  const text = 'Cash collections support a cautiously positive view.'
  return { investment_analysis: { stance: 'cautiously_bullish', confidence: 'medium',
    observation_reason: 'The investment view assumes continued conversion of demand into operating cash flow.',
    points: [{topic: 'thesis', text, evidence_ids: ['s']}], missing_inputs: [] },
    investment_narrative: {language: 'en', sections: [{topic: 'thesis', text, evidence_ids: ['s'],
      review_verdict: 'needs_qualification',
      review_reason: 'The investment view assumes continued conversion of demand into operating cash flow.',
      suggested_revision: 'This view is conditional on continued cash conversion.'}]},
    investment_review: {opinion: 'provisional', review: 'review_completed', verdicts: {'0': 'needs_qualification'}, batch_status: {}},
    providers: {nebius: {calls: 9}} }
}

test('copied reasons and qualifications become one headline condition without mutating data', () => {
  const input = report()
  const original = structuredClone(input)
  assert.deepEqual(headlineQualifications(input), ['This view is conditional on continued cash conversion.'])
  assert.equal(headlineExplanation(input), null)
  assert.deepEqual(input, original)
})

test('technical interruption retains a readable evidence limitation', () => {
  const input = report()
  input.investment_analysis.observation_reason = null
  input.investment_narrative.sections = []
  assert.equal(headlineExplanation(input), incompleteEvidence)
  assert.equal(input.investment_analysis.stance, 'cautiously_bullish')
})

test('instructions copied into observation_reason cannot leak through the headline', () => {
  const input = report()
  input.investment_narrative.sections = []
  input.investment_analysis.observation_reason = 'Review is incomplete. Please check this paragraph.'
  assert.equal(headlineExplanation(input), evidenceQualification)
})

test('neutral or bearish opinions are not altered by presentation', () => {
  for (const stance of ['neutral', 'bearish', 'bullish', 'insufficient']) {
    const input = report()
    input.investment_analysis.stance = stance
    headlineQualifications(input)
    headlineExplanation(input)
    assert.equal(input.investment_analysis.stance, stance)
  }
})

test('pending and approved sections do not become editorial instructions', () => {
  assert.equal(sectionQualification({review_verdict:'pending'}), incompleteEvidence)
  assert.equal(sectionQualification({review_verdict:'supported'}), null)
})
