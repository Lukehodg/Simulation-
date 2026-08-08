using System.Collections.Generic;
using UnityEngine;
using CardioVR.Vasculature;

namespace CardioVR.Catheter
{
    /// The tip's position in the vascular tree: the chain of segments it has
    /// travelled through, plus how far it sits along the last one.
    public class CatheterState
    {
        public readonly List<string> Path = new List<string>();
        public float TipDepthCm;
        public float RollDegrees;

        public Vector3 TipPosition;
        public Vector3 TipTangent;

        /// Newtons of axial resistance felt at the operator's hand.
        public float Resistance;
        public bool IsBuckling;

        public string TipSegmentId => Path.Count > 0 ? Path[Path.Count - 1] : null;

        public float InsertedLengthCm(VesselNetwork network)
        {
            float total = TipDepthCm;
            for (int i = 0; i < Path.Count - 1; i++)
            {
                var parent = network.Get(Path[i]);
                var child = network.Get(Path[i + 1]);
                total += child.branchPointOnParent * parent.LengthCm;
            }
            return total;
        }

        public void ResetTo(string accessSegmentId)
        {
            Path.Clear();
            Path.Add(accessSegmentId);
            TipDepthCm = 0f;
            RollDegrees = 0f;
            Resistance = 0f;
            IsBuckling = false;
        }
    }
}
