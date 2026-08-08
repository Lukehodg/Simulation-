using UnityEngine;
using CardioVR.Physiology;

namespace CardioVR.UI
{
    /// Draws the rhythm strip onto a texture for the in-world patient monitor.
    ///
    /// The trace is synthesised from the current rate and rhythm rather than played
    /// back, so a rate change is visible immediately and an arrhythmia looks like
    /// the arrhythmia rather than a label change. In a cath lab the monitor is
    /// often the first place a complication announces itself, which is why it needs
    /// to be readable across the room and not a UI panel.
    public class PatientMonitorRenderer : MonoBehaviour
    {
        [SerializeField] PatientVitals vitals;
        [SerializeField] Renderer monitorSurface;

        [Header("Trace")]
        [SerializeField] int width = 512;
        [SerializeField] int height = 128;
        [SerializeField] int samplesPerSecond = 220;

        [SerializeField] Color background = new Color(0.02f, 0.03f, 0.04f);
        [SerializeField] Color grid = new Color(0.29f, 0.89f, 0.60f, 0.10f);
        [SerializeField] Color trace = new Color(0.29f, 0.89f, 0.60f);
        [SerializeField] Color alarmTrace = new Color(1f, 0.36f, 0.34f);

        Texture2D texture;
        Color32[] pixels;
        float[] samples;
        int writeHead;
        float phase;
        int beatCount;
        float sampleAccumulator;

        void Awake()
        {
            texture = new Texture2D(width, height, TextureFormat.RGBA32, false)
            {
                filterMode = FilterMode.Bilinear,
                wrapMode = TextureWrapMode.Clamp
            };

            pixels = new Color32[width * height];
            samples = new float[width];

            if (monitorSurface != null) monitorSurface.material.mainTexture = texture;
        }

        void Update()
        {
            Advance(Time.deltaTime);
            Redraw();
        }

        void Advance(float dt)
        {
            float period = 60f / Mathf.Max(25f, vitals.HeartRate);

            sampleAccumulator += dt * samplesPerSecond;
            int count = Mathf.FloorToInt(sampleAccumulator);
            sampleAccumulator -= count;

            for (int i = 0; i < count; i++)
            {
                phase += 1f / samplesPerSecond / period;
                if (phase >= 1f)
                {
                    phase -= 1f;
                    beatCount++;
                }

                bool wideBeat = vitals.Rhythm == Rhythm.VentricularEctopy && beatCount % 5 == 4;
                samples[writeHead] = Sample(phase, vitals.Rhythm, wideBeat);
                writeHead = (writeHead + 1) % width;
            }
        }

        /// One cardiac cycle as a piecewise PQRST, or the rhythm's own signature.
        static float Sample(float t, Rhythm rhythm, bool wide)
        {
            switch (rhythm)
            {
                case Rhythm.VentricularFibrillation:
                    return (Mathf.Sin(t * 47f) + Mathf.Sin(t * 31.3f) * 0.7f + Mathf.Sin(t * 71.1f) * 0.5f) * 0.28f;
                case Rhythm.Asystole:
                    return 0f;
            }

            float w = wide ? 1.9f : 1f;

            if (t < 0.12f) return 0.12f * Mathf.Sin(t / 0.12f * Mathf.PI);   // P
            if (t < 0.19f) return 0f;
            if (t < 0.21f * w) return -0.10f;                                 // Q
            if (t < 0.25f * w) return wide ? 0.72f : 1f;                      // R
            if (t < 0.29f * w) return -0.26f;                                 // S
            if (t < 0.42f) return 0f;
            if (t < 0.60f) return 0.20f * Mathf.Sin((t - 0.42f) / 0.18f * Mathf.PI);  // T
            return 0f;
        }

        void Redraw()
        {
            var bg = (Color32)background;
            for (int i = 0; i < pixels.Length; i++) pixels[i] = bg;

            var gridColor = (Color32)Color.Lerp(background, grid, grid.a * 10f);
            for (int x = 0; x < width; x += 32)
                for (int y = 0; y < height; y++) pixels[y * width + x] = gridColor;
            for (int y = 0; y < height; y += 32)
                for (int x = 0; x < width; x++) pixels[y * width + x] = gridColor;

            var ink = (Color32)(vitals.IsUnstable ? alarmTrace : trace);
            int previous = -1;

            for (int x = 0; x < width; x++)
            {
                int index = (writeHead + x) % width;
                int y = Mathf.Clamp(
                    Mathf.RoundToInt(height * 0.38f + samples[index] * height * 0.42f), 0, height - 1);

                if (previous >= 0)
                {
                    // Join to the previous column so fast deflections stay continuous.
                    int from = Mathf.Min(previous, y);
                    int to = Mathf.Max(previous, y);
                    for (int fill = from; fill <= to; fill++) pixels[fill * width + x] = ink;
                }
                else
                {
                    pixels[y * width + x] = ink;
                }

                previous = y;
            }

            // A gap at the write head, the way a sweeping monitor erases ahead of itself.
            for (int x = width - 6; x < width; x++)
                for (int y = 0; y < height; y++) pixels[y * width + x] = bg;

            texture.SetPixels32(pixels);
            texture.Apply(false);
        }
    }
}
