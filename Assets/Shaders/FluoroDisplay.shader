// Turns the accumulated density buffer into something that reads as a cine run
// on the in-world monitor: inverted to dark-on-light, with image intensifier
// vignetting and the quantum noise that makes low-dose fluoroscopy look grainy.
//
// Grain is tied to dose: screen at a low rate and the image is noisier, which is
// the trade-off the trainee is meant to feel rather than be told about.
Shader "CardioVR/FluoroDisplay"
{
    Properties
    {
        _MainTex ("Density buffer", 2D) = "black" {}
        _FilmColor ("Film base", Color) = (0.80, 0.79, 0.76, 1)
        _InkColor ("Absorbed", Color) = (0.07, 0.08, 0.09, 1)
        _Exposure ("Exposure", Range(0.1, 6)) = 1.6
        _Grain ("Grain", Range(0, 0.3)) = 0.05
        _Vignette ("Vignette", Range(0, 2)) = 0.7
        _Live ("Live", Range(0, 1)) = 1
    }

    SubShader
    {
        Tags { "RenderType" = "Opaque" "Queue" = "Geometry" }
        Cull Back
        ZWrite On

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            sampler2D _MainTex;
            float4 _MainTex_ST;
            fixed4 _FilmColor;
            fixed4 _InkColor;
            float _Exposure;
            float _Grain;
            float _Vignette;
            float _Live;

            struct appdata
            {
                float4 vertex : POSITION;
                float2 uv : TEXCOORD0;
            };

            struct v2f
            {
                float4 pos : SV_POSITION;
                float2 uv : TEXCOORD0;
            };

            v2f vert(appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.uv = TRANSFORM_TEX(v.uv, _MainTex);
                return o;
            }

            float hash(float2 p)
            {
                return frac(sin(dot(p, float2(12.9898, 78.233))) * 43758.5453);
            }

            fixed4 frag(v2f i) : SV_Target
            {
                float density = tex2D(_MainTex, i.uv).r * _Exposure;
                float absorbed = saturate(density);

                fixed3 col = lerp(_FilmColor.rgb, _InkColor.rgb, absorbed);

                // Round image intensifier field.
                float2 centred = i.uv - 0.5;
                float radius = length(centred) * 2.0;
                col *= 1.0 - saturate(pow(radius, 3.0)) * _Vignette;

                float noise = hash(i.uv * 512.0 + frac(_Time.y) * 91.7) - 0.5;
                col += noise * _Grain * _Live;

                // Held image: no live beam, so no noise and a slight wash.
                col = lerp(col * 0.86 + 0.09, col, _Live);

                return fixed4(saturate(col), 1);
            }
            ENDCG
        }
    }

    Fallback Off
}
