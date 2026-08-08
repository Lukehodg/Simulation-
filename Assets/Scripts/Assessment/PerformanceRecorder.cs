using System.Collections.Generic;
using UnityEngine;
using CardioVR.Complications;
using CardioVR.Core;

namespace CardioVR.Assessment
{
    public enum RecordKind
    {
        StepStarted,
        StepCompleted,
        StepSkipped,
        Complication,
        ProcedureCompleted
    }

    public readonly struct PerformanceRecord
    {
        public readonly RecordKind Kind;
        public readonly string Label;
        public readonly float Value;
        public readonly float TimeStamp;

        public PerformanceRecord(RecordKind kind, string label, float value, float timeStamp)
        {
            Kind = kind;
            Label = label;
            Value = value;
            TimeStamp = timeStamp;
        }
    }

    public class PerformanceRecorder : MonoBehaviour
    {
        [SerializeField] ComplicationSystem complications;

        readonly List<PerformanceRecord> records = new List<PerformanceRecord>();

        public IReadOnlyList<PerformanceRecord> Records => records;
        public float StartedAt { get; private set; }
        public float TotalSeconds { get; private set; }
        public int SkippedSteps { get; private set; }
        public int SlowSteps { get; private set; }

        void Awake() => StartedAt = Time.time;

        void OnEnable() => complications.ComplicationOccurred += OnComplication;
        void OnDisable() => complications.ComplicationOccurred -= OnComplication;

        void OnComplication(ComplicationEvent evt)
            => Add(RecordKind.Complication, evt.Type.ToString(), evt.Severity);

        public void RecordStepStarted(ProcedureStep step)
            => Add(RecordKind.StepStarted, step.title, 0f);

        public void RecordStepCompleted(ProcedureStep step, float elapsedSeconds)
        {
            if (step.parTimeSeconds > 0f && elapsedSeconds > step.parTimeSeconds) SlowSteps++;
            Add(RecordKind.StepCompleted, step.title, elapsedSeconds);
        }

        public void RecordStepSkipped(ProcedureStep step)
        {
            SkippedSteps++;
            Add(RecordKind.StepSkipped, step.title, 0f);
        }

        public void RecordProcedureCompleted()
        {
            TotalSeconds = Time.time - StartedAt;
            Add(RecordKind.ProcedureCompleted, "Procedure", TotalSeconds);
        }

        void Add(RecordKind kind, string label, float value)
            => records.Add(new PerformanceRecord(kind, label, value, Time.time - StartedAt));
    }
}
