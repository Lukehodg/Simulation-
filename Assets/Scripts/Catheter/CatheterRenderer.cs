using System.Collections.Generic;
using UnityEngine;
using CardioVR.Vasculature;

namespace CardioVR.Catheter
{
    /// Rebuilds the catheter as a tube each frame: the intravascular portion along
    /// the traversed centrelines, so it appears on fluoroscopy exactly where the
    /// navigator says the tip is.
    ///
    /// Lives in the anatomy's centimetre space, like the vessel meshes.
    [RequireComponent(typeof(MeshFilter), typeof(MeshRenderer))]
    public class CatheterRenderer : MonoBehaviour
    {
        [SerializeField] CatheterNavigator navigator;
        [SerializeField] CatheterProfile profile;

        [Tooltip("Centimetres between sampled points along the shaft.")]
        [SerializeField] float sampleSpacingCm = 0.4f;

        [SerializeField] int ringVertices = 8;

        readonly List<Vector3> centreline = new List<Vector3>();
        readonly List<Vector3> vertices = new List<Vector3>();
        readonly List<Vector3> normals = new List<Vector3>();
        readonly List<int> triangles = new List<int>();

        Mesh mesh;

        void Awake()
        {
            mesh = new Mesh { name = "Catheter" };
            mesh.MarkDynamic();
            GetComponent<MeshFilter>().sharedMesh = mesh;
        }

        void LateUpdate()
        {
            SampleCentreline();
            if (centreline.Count < 2) { mesh.Clear(); return; }
            Extrude(profile.OuterRadiusMm * 0.1f);
        }

        void SampleCentreline()
        {
            centreline.Clear();
            var state = navigator.State;
            var network = navigator.Network;

            for (int i = 0; i < state.Path.Count; i++)
            {
                var segment = network.Get(state.Path[i]);

                // Each segment is traced up to the branch it hands off to, or up to
                // the tip on the last one.
                float end = i == state.Path.Count - 1
                    ? state.TipDepthCm
                    : network.Get(state.Path[i + 1]).branchPointOnParent * segment.LengthCm;

                if (end <= 0f) continue;

                int steps = Mathf.Max(1, Mathf.CeilToInt(end / sampleSpacingCm));
                for (int s = 0; s <= steps; s++)
                    centreline.Add(segment.PointAt(end * s / steps));
            }
        }

        void Extrude(float radiusCm)
        {
            vertices.Clear();
            normals.Clear();
            triangles.Clear();

            Vector3 reference = Vector3.up;
            int count = centreline.Count;

            for (int i = 0; i < count; i++)
            {
                Vector3 tangent = i == 0
                    ? (centreline[1] - centreline[0])
                    : (centreline[i] - centreline[i - 1]);

                if (tangent.sqrMagnitude < 1e-8f) tangent = Vector3.forward;
                tangent.Normalize();

                if (Mathf.Abs(Vector3.Dot(tangent, reference)) > 0.95f) reference = Vector3.right;
                Vector3 side = Vector3.Normalize(Vector3.Cross(tangent, reference));
                Vector3 up = Vector3.Cross(side, tangent);
                reference = up;

                for (int r = 0; r < ringVertices; r++)
                {
                    float angle = r / (float)ringVertices * Mathf.PI * 2f;
                    Vector3 normal = side * Mathf.Cos(angle) + up * Mathf.Sin(angle);
                    vertices.Add(centreline[i] + normal * radiusCm);
                    normals.Add(normal);
                }
            }

            for (int i = 0; i < count - 1; i++)
            {
                int a = i * ringVertices;
                int b = (i + 1) * ringVertices;

                for (int r = 0; r < ringVertices; r++)
                {
                    int r2 = (r + 1) % ringVertices;
                    triangles.Add(a + r); triangles.Add(b + r); triangles.Add(a + r2);
                    triangles.Add(a + r2); triangles.Add(b + r); triangles.Add(b + r2);
                }
            }

            mesh.Clear();
            mesh.SetVertices(vertices);
            mesh.SetNormals(normals);
            mesh.SetTriangles(triangles, 0);
            mesh.RecalculateBounds();
        }
    }
}
