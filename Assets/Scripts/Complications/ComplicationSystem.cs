using System;
using System.Collections.Generic;
using UnityEngine;
using CardioVR.Catheter;
using CardioVR.Physiology;
using CardioVR.Vasculature;

namespace CardioVR.Complications
{
    public enum ComplicationType
    {
        VesselDissection,
        Perforation,
        CoronaryDamping,
        AirEmbolism,
        VasovagalReaction,
        ContrastInducedArrhythmia,
        AccessSiteBleed
    }

    public readonly struct ComplicationEvent
    {
        public readonly ComplicationType Type;
        public readonly string SegmentId;
        public readonly float Severity;
        public readonly float TimeStamp;

        public ComplicationEvent(ComplicationType type, string segmentId, float severity, float timeStamp)
        {
            Type = type;
            SegmentId = segmentId;
            Severity = severity;
            TimeStamp = timeStamp;
        }
    }

    /// Accumulates trauma per vessel and converts it into complications once the
    /// vessel's tolerance is exceeded. Trauma is cumulative and does not heal —
    /// rough handling early costs the trainee later, which is the teaching point.
    public class ComplicationSystem : MonoBehaviour
    {
        [SerializeField] CatheterNavigator navigator;
        [SerializeField] PatientVitals vitals;

        [Header("Thresholds")]
        [SerializeField] float dissectionTrauma = 1.0f;
        [SerializeField] float perforationTrauma = 2.2f;
        [SerializeField] float dampingDwellSeconds = 20f;

        readonly Dictionary<string, float> traumaBySegment = new Dictionary<string, float>();
        readonly HashSet<string> alreadyDissected = new HashSet<string>();
        readonly List<ComplicationEvent> log = new List<ComplicationEvent>();

        float coronaryDwellSeconds;
        float bloodLossMl;

        public IReadOnlyList<ComplicationEvent> Log => log;
        public event Action<ComplicationEvent> ComplicationOccurred;

        void OnEnable()
        {
            navigator.WallContact += OnWallContact;
            navigator.Buckled += OnBuckled;
        }

        void OnDisable()
        {
            navigator.WallContact -= OnWallContact;
            navigator.Buckled -= OnBuckled;
        }

        void Update()
        {
            var tip = navigator.State.TipSegmentId;
            var segment = navigator.TipSegment;

            if (segment != null && segment.isCoronary)
            {
                coronaryDwellSeconds += Time.deltaTime;

                // A catheter parked in an ostium obstructs flow; pressure damping
                // then ischaemia. The cue is on the monitor, not the fluoro image.
                if (coronaryDwellSeconds > dampingDwellSeconds)
                {
                    float severity = Mathf.Clamp01((coronaryDwellSeconds - dampingDwellSeconds) / 25f);
                    vitals.ApplyIschaemicInsult(severity);
                    Raise(ComplicationType.CoronaryDamping, tip, severity);
                }
            }
            else
            {
                coronaryDwellSeconds = Mathf.Max(0f, coronaryDwellSeconds - Time.deltaTime * 2f);
            }

            if (bloodLossMl > 0f) vitals.ApplyBloodLoss(bloodLossMl);
        }

        void OnWallContact(VesselSegment segment, float overlapMm)
        {
            float delta = overlapMm * Time.deltaTime * (1f + segment.tortuosity);
            traumaBySegment.TryGetValue(segment.id, out float trauma);
            trauma += delta;
            traumaBySegment[segment.id] = trauma;

            if (trauma > perforationTrauma)
            {
                bloodLossMl += 140f;
                Raise(ComplicationType.Perforation, segment.id, Mathf.Clamp01(trauma / perforationTrauma));
            }
            else if (trauma > dissectionTrauma && alreadyDissected.Add(segment.id))
            {
                Raise(ComplicationType.VesselDissection, segment.id, Mathf.Clamp01(trauma / perforationTrauma));
                if (segment.isCoronary) vitals.ApplyIschaemicInsult(0.55f);
            }
        }

        void OnBuckled()
        {
            var tip = navigator.State.TipSegmentId;
            traumaBySegment.TryGetValue(tip, out float trauma);
            traumaBySegment[tip] = trauma + 0.15f;
        }

        public void NotifySheathInsertion(float force)
        {
            if (force < 6f) return;
            float severity = Mathf.InverseLerp(6f, 14f, force);
            vitals.ApplyVasovagalResponse(severity);
            Raise(ComplicationType.VasovagalReaction, "access", severity);
        }

        public void NotifyContrastInjection(float volumeMl, bool airPurged)
        {
            vitals.ApplyContrastBolus(volumeMl);

            if (!airPurged)
                Raise(ComplicationType.AirEmbolism, navigator.State.TipSegmentId, 0.8f);

            if (volumeMl > 10f)
                Raise(ComplicationType.ContrastInducedArrhythmia, navigator.State.TipSegmentId,
                      Mathf.Clamp01((volumeMl - 10f) / 8f));
        }

        public float TraumaFor(string segmentId)
            => traumaBySegment.TryGetValue(segmentId, out float t) ? t : 0f;

        public float TotalBloodLossMl => bloodLossMl;

        void Raise(ComplicationType type, string segmentId, float severity)
        {
            var evt = new ComplicationEvent(type, segmentId, severity, Time.time);
            log.Add(evt);
            ComplicationOccurred?.Invoke(evt);
        }
    }
}
