using System.Collections.Generic;
using System.Linq;
using UnityEngine;

namespace CardioVR.Vasculature
{
    [CreateAssetMenu(menuName = "CardioVR/Vessel Network", fileName = "VesselNetwork")]
    public class VesselNetwork : ScriptableObject
    {
        public string accessSegmentId = "femoral_r";
        public List<VesselSegment> segments = new List<VesselSegment>();

        Dictionary<string, VesselSegment> byId;
        Dictionary<string, List<VesselSegment>> childrenByParent;

        public void Bake()
        {
            byId = new Dictionary<string, VesselSegment>(segments.Count);
            childrenByParent = new Dictionary<string, List<VesselSegment>>();

            foreach (var s in segments)
            {
                s.Bake();
                byId[s.id] = s;
            }

            foreach (var s in segments)
            {
                if (string.IsNullOrEmpty(s.parentId)) continue;
                if (!childrenByParent.TryGetValue(s.parentId, out var list))
                    childrenByParent[s.parentId] = list = new List<VesselSegment>();
                list.Add(s);
            }

            foreach (var list in childrenByParent.Values)
                list.Sort((a, b) => a.branchPointOnParent.CompareTo(b.branchPointOnParent));
        }

        public VesselSegment Get(string id)
        {
            if (byId == null) Bake();
            return byId.TryGetValue(id, out var s) ? s : null;
        }

        public IReadOnlyList<VesselSegment> ChildrenOf(string parentId)
        {
            if (childrenByParent == null) Bake();
            return childrenByParent.TryGetValue(parentId, out var list)
                ? (IReadOnlyList<VesselSegment>)list
                : System.Array.Empty<VesselSegment>();
        }

        public IEnumerable<VesselSegment> Coronaries => segments.Where(s => s.isCoronary);
    }
}
