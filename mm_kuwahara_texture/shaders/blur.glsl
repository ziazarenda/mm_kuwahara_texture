
vec4 blur_tensor(vec2 uv) {
    float sigma = max(0.000001, uniformity);
    vec4 value = vec4(0.0); float sum_weights = 0.0;
    for (float i=-50.0; i<=50.0; i+=1.0) {
        float weight = exp(-0.5*pow(i/sigma,2.0)) /
        (6.28318530718*sigma*sigma);
        value += sample_tensor(uv+blur_axis*(i/resolution))*weight;
        sum_weights += weight;
    }
    return value/sum_weights;
}
