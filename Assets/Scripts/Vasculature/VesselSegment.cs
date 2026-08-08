using System;
using System.Collections.Generic;
using UnityEngine;

namespace CardioVR.Vasculature
{
    /// Centreline coordinates are in centimetres, in anatomy-local space.
    [Serializable]
    public class VesselSegment
    {
        public string id;
        public string displayName;
        public string parentId;

        [Range(0f, 1f)] public float branchPointOnParent = 1f;

        public float ostiumRollDegrees;
        public float ostiumRollToleranceDegrees = 25f;

        public float lumenRadiusMm = 2f;
        [Range(0f, 1f)] public float tortuosity;
        [Range(0f, 1f)] public float stenosis;

        public bool isCoronary;
        public List<Vector3> centerline = new List<Vector3>();

        float[] arcLengths;

        public float LengthCm { get; private set; }

        public void Bake()
        {
            if (centerline.Count < 2)
                throw new InvalidOperationException($"Vessel '{id}' needs at least two centreline points.");

            arcLengths = new float[centerline.Count];
            arcLengths[0] = 0f;
            for (int i = 1; i < centerline.Count; i++)
                arcLengths[i] = arcLengths[i - 1] + Vector3.Distance(centerline[i - 1], centerline[i]);

            LengthCm = arcLengths[arcLengths.Length - 1];
        }

        public Vector3 PointAt(float cm)
        {
            FindSpan(cm, out int i, out float t);
            return Vector3.Lerp(centerline[i], centerline[i + 1], t);
        }

        public Vector3 TangentAt(float cm)
        {
            FindSpan(cm, out int i, out _);
            return (centerline[i + 1] - centerline[i]).normalized;
        }

        /// Effective lumen at a point, accounting for a stenosis modelled at mid-segment.
        public float LumenRadiusAt(float cm)
        {
            if (stenosis <= 0f) return lumenRadiusMm;
            float mid = LengthCm * 0.5f;
            float spread = Mathf.Max(LengthCm * 0.15f, 0.5f);
            float falloff = Mathf.Exp(-Mathf.Pow((cm - mid) / spread, 2f));
            return lumenRadiusMm * (1f - stenosis * falloff);
        }

        void FindSpan(float cm, out int index, out float t)
        {
            float clamped = Mathf.Clamp(cm, 0f, LengthCm);
            int hi = arcLengths.Length - 1;
            int lo = 0;
            while (lo < hi - 1)
            {
                int mid = (lo + hi) / 2;
                if (arcLengths[mid] <= clamped) lo = mid; else hi = mid;
            }

            index = lo;
            float span = arcLengths[lo + 1] - arcLengths[lo];
            t = span > Mathf.Epsilon ? (clamped - arcLengths[lo]) / span : 0f;
        }
    }
}
