using System.Collections.Generic;
using UnityEngine;

namespace CardioVR.Vasculature
{
    /// Builds tube meshes from vessel centrelines so the tree can be rendered into
    /// the fluoroscopy camera.
    ///
    /// Meshes are built in the dataset's own units — centimetres — so the anatomy
    /// root is expected to carry a 0.01 scale to bring it into Unity metres. That
    /// keeps the mesh, the navigator's tip position and the JSON all in one frame.
    public static class VesselMeshBuilder
    {
        public static Mesh Build(VesselSegment segment, int ringVertices = 10, int samples = 28)
        {
            var vertices = new List<Vector3>((samples + 1) * ringVertices);
            var normals = new List<Vector3>(vertices.Capacity);
            var triangles = new List<int>(samples * ringVertices * 6);

            Vector3 reference = Vector3.up;

            for (int s = 0; s <= samples; s++)
            {
                float cm = segment.LengthCm * s / samples;
                Vector3 centre = segment.PointAt(cm);
                Vector3 tangent = segment.TangentAt(cm).normalized;

                if (Mathf.Abs(Vector3.Dot(tangent, reference)) > 0.95f) reference = Vector3.right;
                Vector3 side = Vector3.Normalize(Vector3.Cross(tangent, reference));
                Vector3 up = Vector3.Cross(side, tangent);
                reference = up;

                // Lumen radius is in millimetres; the mesh is in centimetres.
                float radiusCm = segment.LumenRadiusAt(cm) * 0.1f;

                for (int r = 0; r < ringVertices; r++)
                {
                    float angle = r / (float)ringVertices * Mathf.PI * 2f;
                    Vector3 normal = side * Mathf.Cos(angle) + up * Mathf.Sin(angle);
                    vertices.Add(centre + normal * radiusCm);
                    normals.Add(normal);
                }
            }

            for (int s = 0; s < samples; s++)
            {
                int a = s * ringVertices;
                int b = (s + 1) * ringVertices;

                for (int r = 0; r < ringVertices; r++)
                {
                    int r2 = (r + 1) % ringVertices;
                    triangles.Add(a + r); triangles.Add(b + r); triangles.Add(a + r2);
                    triangles.Add(a + r2); triangles.Add(b + r); triangles.Add(b + r2);
                }
            }

            var mesh = new Mesh { name = "Vessel_" + segment.id };
            mesh.SetVertices(vertices);
            mesh.SetNormals(normals);
            mesh.SetTriangles(triangles, 0);
            mesh.RecalculateBounds();
            return mesh;
        }

        /// Instantiates the whole tree under a parent, one renderer per vessel.
        public static Dictionary<string, GameObject> BuildTree(
            VesselNetwork network, Transform parent, Material material, int layer)
        {
            network.Bake();
            var built = new Dictionary<string, GameObject>(network.segments.Count);

            foreach (var segment in network.segments)
            {
                var go = new GameObject(segment.id) { layer = layer };
                go.transform.SetParent(parent, false);

                go.AddComponent<MeshFilter>().sharedMesh = Build(segment);

                var renderer = go.AddComponent<MeshRenderer>();
                renderer.sharedMaterial = material;
                renderer.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                renderer.receiveShadows = false;

                built[segment.id] = go;
            }

            return built;
        }
    }
}
