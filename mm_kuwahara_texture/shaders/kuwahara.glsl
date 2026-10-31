// The MIT License - Copyright (c) 2022 Garrett Gunnell
// Adapted from https://github.com/GarrettGunnell/Post-Processing/
// blob/871a7ffcc44174f237b4c0127db51026c438d7a4/Assets/Kuwahara%20Filter/AnisotropicKuwahara.shader

vec3 filter_kuwahara(vec2 uv, int size, float _sharpness, float _ecc) {
    float sharpness = mix(3.0, 18.0, clamp(_sharpness, 0.0, 1.0));
    int _KernelSize = max(0, size * 2);
    const float alpha = 1.0; const float _Hardness = 8.0; const float _ZeroCrossing = 0.58;
    float zeta = 1.0 / (float(_KernelSize) / 2.0);

    float ecc = mix(0.7, 1.5, clamp(_ecc, 0.0, 1.0));

    vec4 g = sample_tensor(uv);
    float lambda1 = 0.5 * (g.y + g.x + sqrt(g.y * g.y - 2.0 * g.x * g.y + g.x * g.x + 4.0 * g.z * g.z));
    float lambda2 = 0.5 * (g.y + g.x - sqrt(g.y * g.y - 2.0 * g.x * g.y + g.x * g.x + 4.0 * g.z * g.z));

    vec2 v = vec2(lambda1 - g.x, -g.z);
    float A = (lambda1 + lambda2 > 0.0) ? (lambda1 - lambda2) / (lambda1 + lambda2) : 0.0;
    vec4 t = vec4(length(v) > 0.0 ? normalize(v) : vec2(0.0, 1.0), 0.0, A);

    int kernelRadius = _KernelSize / 2;
    float a = float(kernelRadius) * clamp((alpha + t.w) / alpha, 0.1, 2.0);
    float b = float(kernelRadius) * clamp(alpha / (alpha + t.w), 0.1, 2.0);

    float cos_phi = t.x;
    float sin_phi = t.y;

    mat2 R = mat2(vec2(cos_phi, -sin_phi), vec2(sin_phi, cos_phi));
    mat2 S = mat2(vec2(0.5 / a / ecc, 0.0), vec2(0.0, 0.5 / b * ecc));

    mat2 SR = S * R;

    int max_x = int(sqrt(a * a * cos_phi * cos_phi + b * b * sin_phi * sin_phi));
    int max_y = int(sqrt(a * a * sin_phi * sin_phi + b * b * cos_phi * cos_phi));

    float zeroCross = _ZeroCrossing;
    float sinZeroCross = sin(zeroCross);
    float eta = (zeta + cos(zeroCross)) / (sinZeroCross * sinZeroCross);

    vec4 m[8];
    vec3 s[8];

    for (int k = 0; k < 8; ++k) {
        m[k] = vec4(0.0);
        s[k] = vec3(0.0);
    }

    for (int y = -max_y; y <= max_y; ++y) {
        for (int x = -max_x; x <= max_x; ++x) {
            vec2 v = SR * vec2(float(x), float(y));
            if (dot(v, v) <= 0.125) {
                vec3 c = sample_color( uv + vec2(float(x), float(y)) * 1.0/vec2(resolution) ).rgb;
                c = clamp(c, vec3(0.0), vec3(1.0));
                float sum = 0.0;
                float w[8];
                float z, vxx, vyy;

                // Calculate Polynomial Weights
                vxx = zeta - eta * v.x * v.x;
                vyy = zeta - eta * v.y * v.y;
                z = max(0.0, v.y + vxx);
                w[0] = z * z;
                sum += w[0];
                z = max(0.0, -v.x + vyy);
                w[2] = z * z;
                sum += w[2];
                z = max(0.0, -v.y + vxx);
                w[4] = z * z;
                sum += w[4];
                z = max(0.0, v.x + vyy);
                w[6] = z * z;
                sum += w[6];
                v = sqrt(2.0) / 2.0 * vec2(v.x - v.y, v.x + v.y);
                vxx = zeta - eta * v.x * v.x;
                vyy = zeta - eta * v.y * v.y;
                z = max(0.0, v.y + vxx);
                w[1] = z * z;
                sum += w[1];
                z = max(0.0, -v.x + vyy);
                w[3] = z * z;
                sum += w[3];
                z = max(0.0, -v.y + vxx);
                w[5] = z * z;
                sum += w[5];
                z = max(0.0, v.x + vyy);
                w[7] = z * z;
                sum += w[7];

                float gaussian_weight = exp(-3.125 * dot(v, v)) / sum;

                for (int k = 0; k < 8; ++k) {
                    float wk = w[k] * gaussian_weight;
                    m[k] += vec4(c * wk, wk);
                    s[k] += c * c * wk;
                }
            }
        }
    }

    vec4 result = vec4(0.0);
    for (int k = 0; k < 8; ++k) {
        m[k].rgb /= m[k].w;
        s[k] = abs(s[k] / m[k].w - m[k].rgb * m[k].rgb);

        float sigma2 = s[k].r + s[k].g + s[k].b;
        float w = 1.0 / (1.0 + pow(_Hardness * 1000.0 * sigma2, 0.5 * sharpness));

        result += vec4(m[k].rgb * w, w);
    }

    return clamp(result.rgb / result.w, vec3(0.0), vec3(1.0));
}
