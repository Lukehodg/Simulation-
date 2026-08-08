using System;
using System.Collections.Generic;
using UnityEngine;

namespace CardioVR.Vasculature
{
    /// Builds a network from JSON so anatomy can be authored and reviewed outside
    /// Unity — clinicians can edit the dataset without opening the editor.
    public static class VesselNetworkLoader
    {
        [Serializable]
        class Payload
        {
            public string accessSegmentId;
            public List<VesselSegment> segments;
        }

        public static VesselNetwork FromJson(string json)
        {
            var payload = JsonUtility.FromJson<Payload>(json);
            if (payload?.segments == null || payload.segments.Count == 0)
                throw new ArgumentException("Vessel network JSON contained no segments.");

            var network = ScriptableObject.CreateInstance<VesselNetwork>();
            network.accessSegmentId = payload.accessSegmentId;
            network.segments = payload.segments;

            Validate(network);
            network.Bake();
            return network;
        }

        public static VesselNetwork FromResource(string resourcePath)
        {
            var asset = Resources.Load<TextAsset>(resourcePath);
            if (asset == null)
                throw new ArgumentException($"No vessel network at Resources/{resourcePath}.");

            return FromJson(asset.text);
        }

        static void Validate(VesselNetwork network)
        {
            var ids = new HashSet<string>();
            foreach (var s in network.segments)
            {
                if (string.IsNullOrEmpty(s.id))
                    throw new ArgumentException("A vessel segment is missing its id.");
                if (!ids.Add(s.id))
                    throw new ArgumentException($"Duplicate vessel id '{s.id}'.");
            }

            foreach (var s in network.segments)
                if (!string.IsNullOrEmpty(s.parentId) && !ids.Contains(s.parentId))
                    throw new ArgumentException($"Vessel '{s.id}' names unknown parent '{s.parentId}'.");

            if (!ids.Contains(network.accessSegmentId))
                throw new ArgumentException($"Access segment '{network.accessSegmentId}' is not in the network.");
        }
    }
}
