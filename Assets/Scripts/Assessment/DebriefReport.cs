using System.Collections.Generic;
using System.Text;
using UnityEngine;
using CardioVR.Complications;
using CardioVR.Core;
using CardioVR.UI;

namespace CardioVR.Assessment
{
    public readonly struct ScoreLine
    {
        public readonly string Metric;
        public readonly string Actual;
        public readonly string Target;
        public readonly int Points;
        public readonly int MaxPoints;

        public ScoreLine(string metric, string actual, string target, int points, int maxPoints)
        {
            Metric = metric;
            Actual = actual;
            Target = target;
            Points = points;
            MaxPoints = maxPoints;
        }
    }

    /// Turns the run into a scored debrief. Weighting is opinionated: patient
    /// safety outweighs speed, and any complication caps the achievable grade.
    public class DebriefReport : MonoBehaviour
    {
        [SerializeField] ProcedureDefinition procedure;
        [SerializeField] PerformanceRecorder recorder;
        [SerializeField] FluoroscopyController fluoroscopy;
        [SerializeField] ComplicationSystem complications;

        public List<ScoreLine> Build()
        {
            var lines = new List<ScoreLine>
            {
                ScoreAgainstTarget("Fluoroscopy time", fluoroscopy.FluoroSeconds,
                    procedure.targetFluoroSeconds, "s", 20),

                ScoreAgainstTarget("Contrast volume", fluoroscopy.ContrastUsedMl,
                    procedure.targetContrastMl, "ml", 20),

                ScoreAgainstTarget("Radiation dose", fluoroscopy.CumulativeDoseMilliGray,
                    procedure.targetDoseMilliGray, "mGy", 15),

                ScoreComplications(),
                ScoreCompleteness(),
                ScoreEfficiency()
            };

            return lines;
        }

        public int TotalScore()
        {
            int total = 0;
            foreach (var line in Build()) total += line.Points;
            return total;
        }

        public string Grade()
        {
            int score = TotalScore();
            bool anyMajor = false;
            foreach (var c in complications.Log)
                if (c.Type == ComplicationType.Perforation
                    || c.Type == ComplicationType.AirEmbolism
                    || c.Severity > 0.7f)
                    anyMajor = true;

            if (anyMajor) return score >= 70 ? "Borderline — major complication" : "Unsatisfactory";
            if (score >= 90) return "Excellent";
            if (score >= 75) return "Competent";
            if (score >= 60) return "Needs practice";
            return "Unsatisfactory";
        }

        ScoreLine ScoreAgainstTarget(string metric, float actual, float target, string unit, int max)
        {
            float ratio = target > 0f ? actual / target : 0f;
            int points = Mathf.RoundToInt(Mathf.Clamp01(Mathf.InverseLerp(2f, 1f, ratio)) * max);
            if (ratio <= 1f) points = max;

            return new ScoreLine(metric, $"{actual:0.#}{unit}", $"≤ {target:0.#}{unit}", points, max);
        }

        ScoreLine ScoreComplications()
        {
            const int max = 30;
            float penalty = 0f;
            foreach (var c in complications.Log)
                penalty += c.Severity * PenaltyWeight(c.Type);

            int points = Mathf.Clamp(max - Mathf.RoundToInt(penalty), 0, max);
            string actual = complications.Log.Count == 0 ? "none" : $"{complications.Log.Count} event(s)";
            return new ScoreLine("Complications", actual, "none", points, max);
        }

        /// How much each complication costs. Anything that puts blood or air
        /// somewhere it should not be is scored as career-defining; the rest scale
        /// with how recoverable they are.
        static float PenaltyWeight(ComplicationType type)
        {
            switch (type)
            {
                case ComplicationType.Perforation: return 30f;
                case ComplicationType.AirEmbolism: return 30f;
                case ComplicationType.VesselDissection: return 20f;
                case ComplicationType.CoronaryDamping: return 12f;
                case ComplicationType.ContrastInducedArrhythmia: return 10f;
                case ComplicationType.VasovagalReaction: return 8f;
                case ComplicationType.AccessSiteBleed: return 8f;
                default: return 5f;
            }
        }

        ScoreLine ScoreCompleteness()
        {
            const int max = 10;
            int points = Mathf.Clamp(max - recorder.SkippedSteps * 4, 0, max);
            return new ScoreLine("Steps completed", $"{recorder.SkippedSteps} skipped", "0 skipped", points, max);
        }

        ScoreLine ScoreEfficiency()
        {
            const int max = 5;
            int points = Mathf.Clamp(max - recorder.SlowSteps, 0, max);
            return new ScoreLine("Pace", $"{recorder.SlowSteps} step(s) over par", "0 over par", points, max);
        }

        public string ToPlainText()
        {
            var sb = new StringBuilder();
            sb.AppendLine(procedure.displayName);
            sb.AppendLine($"Total time: {recorder.TotalSeconds:0}s");
            sb.AppendLine();

            foreach (var line in Build())
                sb.AppendLine($"{line.Metric,-22} {line.Actual,-14} target {line.Target,-14} {line.Points}/{line.MaxPoints}");

            sb.AppendLine();
            sb.AppendLine($"Score: {TotalScore()}/100 — {Grade()}");

            if (complications.Log.Count > 0)
            {
                sb.AppendLine();
                sb.AppendLine("Complications:");
                foreach (var c in complications.Log)
                    sb.AppendLine($"  {c.TimeStamp:0}s  {c.Type} in {c.SegmentId} (severity {c.Severity:0.00})");
            }

            return sb.ToString();
        }
    }
}
