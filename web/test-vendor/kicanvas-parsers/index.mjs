var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// src/base/color.ts
var Color = class _Color {
  constructor(r, g, b, a = 1) {
    this.r = r;
    this.g = g;
    this.b = b;
    this.a = a;
  }
  static {
    __name(this, "Color");
  }
  copy() {
    return new _Color(this.r, this.g, this.b, this.a);
  }
  static get transparent_black() {
    return new _Color(0, 0, 0, 0);
  }
  static get black() {
    return new _Color(0, 0, 0, 1);
  }
  static get white() {
    return new _Color(1, 1, 1, 1);
  }
  static from_css(str) {
    let r, g, b, a;
    if (str[0] == "#") {
      str = str.slice(1);
      if (str.length == 3) {
        str = `${str[0]}${str[0]}${str[1]}${str[1]}${str[2]}${str[2]}`;
      }
      if (str.length == 6) {
        str = `${str}FF`;
      }
      [r, g, b, a] = [
        parseInt(str.slice(0, 2), 16) / 255,
        parseInt(str.slice(2, 4), 16) / 255,
        parseInt(str.slice(4, 6), 16) / 255,
        parseInt(str.slice(6, 8), 16) / 255
      ];
    } else if (str.startsWith("rgb")) {
      if (!str.startsWith("rgba")) {
        str = `rgba(${str.slice(4, -1)}, 1)`;
      }
      str = str.trim().slice(5, -1);
      const parts = str.split(",");
      if (parts.length != 4) {
        throw new Error(`Invalid color ${str}`);
      }
      [r, g, b, a] = [
        parseFloat(parts[0]) / 255,
        parseFloat(parts[1]) / 255,
        parseFloat(parts[2]) / 255,
        parseFloat(parts[3])
      ];
    } else {
      throw new Error(`Unable to parse CSS color string ${str}`);
    }
    return new _Color(r, g, b, a);
  }
  to_css() {
    return `rgba(${this.r_255}, ${this.g_255}, ${this.b_255}, ${this.a})`;
  }
  to_array() {
    return [this.r, this.g, this.b, this.a];
  }
  get r_255() {
    return Math.round(this.r * 255);
  }
  set r_255(v) {
    this.r = v / 255;
  }
  get g_255() {
    return Math.round(this.g * 255);
  }
  set g_255(v) {
    this.g = v / 255;
  }
  get b_255() {
    return Math.round(this.b * 255);
  }
  set b_255(v) {
    this.b = v / 255;
  }
  get is_transparent_black() {
    return this.r == 0 && this.g == 0 && this.b == 0 && this.a == 0;
  }
  with_alpha(a) {
    const c = this.copy();
    c.a = a;
    return c;
  }
  desaturate() {
    if (this.r == this.g && this.r == this.b) {
      return this;
    }
    const [h, _, l] = rgb_to_hsl(this.r, this.g, this.b);
    return new _Color(...hsl_to_rgb(h, 0, l));
  }
  mix(other, amount) {
    return new _Color(
      other.r * (1 - amount) + this.r * amount,
      other.g * (1 - amount) + this.g * amount,
      other.b * (1 - amount) + this.b * amount,
      this.a
    );
  }
};
function rgb_to_hsl(r, g, b) {
  const max = Math.max(...[r, g, b]);
  const min = Math.min(...[r, g, b]);
  const l = (min + max) / 2;
  const d = max - min;
  let [h, s] = [NaN, 0];
  if (d !== 0) {
    s = l === 0 || l === 1 ? 0 : (max - l) / Math.min(l, 1 - l);
    switch (max) {
      case r:
        h = (g - b) / d + (g < b ? 6 : 0);
        break;
      case g:
        h = (b - r) / d + 2;
        break;
      case b:
        h = (r - g) / d + 4;
    }
    h = h * 60;
  }
  return [h, s * 100, l * 100];
}
__name(rgb_to_hsl, "rgb_to_hsl");
function hsl_to_rgb(h, s, l) {
  h = h % 360;
  if (h < 0) {
    h += 360;
  }
  s /= 100;
  l /= 100;
  function f(n) {
    const k = (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  }
  __name(f, "f");
  return [f(0), f(8), f(4)];
}
__name(hsl_to_rgb, "hsl_to_rgb");

// src/base/log.ts
var Logger = class {
  constructor(name, level = 1 /* INFO */) {
    this.name = name;
    this.level = level;
  }
  static {
    __name(this, "Logger");
  }
  #log(method, ...args) {
    method(
      `%c${this.name}:%c`,
      `color: ButtonText`,
      `color: inherit`,
      ...args
    );
  }
  debug(...args) {
    if (this.level >= 2 /* DEBUG */) {
      this.#log(console.debug, ...args);
    }
  }
  info(...args) {
    if (this.level >= 1 /* INFO */) {
      this.#log(console.info.bind(window.console), ...args);
    }
  }
  warn(...args) {
    if (this.level >= 0 /* ERROR */) {
      this.#log(console.warn, ...args);
    }
  }
  error(...args) {
    if (this.level >= 0 /* ERROR */) {
      this.#log(console.error, ...args);
    }
  }
};
var default_logger = new Logger("kicanvas");
function warn(...args) {
  default_logger.warn(...args);
}
__name(warn, "warn");

// src/base/types.ts
function is_string(value) {
  return typeof value === "string";
}
__name(is_string, "is_string");
function is_number(value) {
  return typeof value === "number" && !isNaN(value);
}
__name(is_number, "is_number");
function is_array(value) {
  return Array.isArray(value);
}
__name(is_array, "is_array");

// src/base/math/matrix3.ts
var Matrix3 = class _Matrix3 {
  static {
    __name(this, "Matrix3");
  }
  /**
   * Create a new Matrix
   * @param elements the 9 matrix elements
   */
  constructor(elements) {
    if (elements.length != 9) {
      throw new Error(`Matrix3 requires 9 elements, got ${elements}`);
    }
    this.elements = new Float32Array(elements);
  }
  /**
   * Create a Matrix3 from a DOMMatrix
   */
  static from_DOMMatrix(m) {
    return new _Matrix3([
      m.m11,
      m.m12,
      m.m14,
      m.m21,
      m.m22,
      m.m24,
      m.m41,
      m.m42,
      m.m44
    ]);
  }
  /**
   * Create a DOMMatrix from this Matrix3
   */
  to_DOMMatrix() {
    const e = this.elements;
    return new DOMMatrix([
      e[0],
      e[3],
      e[1],
      e[4],
      e[6],
      e[7]
    ]);
  }
  /**
   * Create a 4x4 DOMMatrix from this Matrix3
   */
  to_4x4_DOMMatrix() {
    const e = this.elements;
    return new DOMMatrix([
      e[0],
      e[1],
      0,
      e[2],
      e[3],
      e[4],
      0,
      e[5],
      0,
      0,
      1,
      0,
      e[6],
      e[7],
      0,
      1
    ]);
  }
  /**
   * @returns a new identity matrix
   */
  static identity() {
    return new _Matrix3([
      1,
      0,
      0,
      0,
      1,
      0,
      0,
      0,
      1
    ]);
  }
  /**
   * @returns a new matrix representing a 2D orthographic projection
   */
  static orthographic(width, height) {
    return new _Matrix3([
      2 / width,
      0,
      0,
      0,
      -2 / height,
      0,
      -1,
      1,
      1
    ]);
  }
  /**
   * @returns a copy of this matrix
   */
  copy() {
    return new _Matrix3(this.elements);
  }
  /**
   * Update this matrix's elements
   */
  set(elements) {
    if (elements.length != 9) {
      throw new Error(`Matrix3 requires 9 elements, got ${elements}`);
    }
    this.elements.set(elements);
  }
  /**
   * Transform a vector by multiplying it with this matrix.
   * @returns A new Vec2
   */
  transform(vec) {
    const x1 = this.elements[0 * 3 + 0];
    const x2 = this.elements[0 * 3 + 1];
    const y1 = this.elements[1 * 3 + 0];
    const y2 = this.elements[1 * 3 + 1];
    const z1 = this.elements[2 * 3 + 0];
    const z2 = this.elements[2 * 3 + 1];
    const px = vec.x;
    const py = vec.y;
    const x = px * x1 + py * y1 + z1;
    const y = px * x2 + py * y2 + z2;
    return new Vec2(x, y);
  }
  /**
   * Transforms a list of vectors
   * @yields new transformed vectors
   */
  *transform_all(vecs) {
    for (const vec of vecs) {
      yield this.transform(vec);
    }
  }
  /**
   * Transforms a list of vectors by a given matrix, which may be null.
   */
  static transform_all(mat, vecs) {
    if (!mat) {
      return vecs;
    }
    return Array.from(mat.transform_all(vecs));
  }
  /**
   * Multiply this matrix by another and store the result
   * in this matrix.
   * @returns this matrix
   */
  multiply_self(b) {
    const a00 = this.elements[0 * 3 + 0];
    const a01 = this.elements[0 * 3 + 1];
    const a02 = this.elements[0 * 3 + 2];
    const a10 = this.elements[1 * 3 + 0];
    const a11 = this.elements[1 * 3 + 1];
    const a12 = this.elements[1 * 3 + 2];
    const a20 = this.elements[2 * 3 + 0];
    const a21 = this.elements[2 * 3 + 1];
    const a22 = this.elements[2 * 3 + 2];
    const b00 = b.elements[0 * 3 + 0];
    const b01 = b.elements[0 * 3 + 1];
    const b02 = b.elements[0 * 3 + 2];
    const b10 = b.elements[1 * 3 + 0];
    const b11 = b.elements[1 * 3 + 1];
    const b12 = b.elements[1 * 3 + 2];
    const b20 = b.elements[2 * 3 + 0];
    const b21 = b.elements[2 * 3 + 1];
    const b22 = b.elements[2 * 3 + 2];
    this.elements[0] = b00 * a00 + b01 * a10 + b02 * a20;
    this.elements[1] = b00 * a01 + b01 * a11 + b02 * a21;
    this.elements[2] = b00 * a02 + b01 * a12 + b02 * a22;
    this.elements[3] = b10 * a00 + b11 * a10 + b12 * a20;
    this.elements[4] = b10 * a01 + b11 * a11 + b12 * a21;
    this.elements[5] = b10 * a02 + b11 * a12 + b12 * a22;
    this.elements[6] = b20 * a00 + b21 * a10 + b22 * a20;
    this.elements[7] = b20 * a01 + b21 * a11 + b22 * a21;
    this.elements[8] = b20 * a02 + b21 * a12 + b22 * a22;
    return this;
  }
  /**
   * Create a new matrix by multiplying this matrix with another
   * @returns a new matrix
   */
  multiply(b) {
    return this.copy().multiply_self(b);
  }
  /**
   * @returns A new matrix that is the inverse of this matrix
   */
  inverse() {
    const a00 = this.elements[0 * 3 + 0];
    const a01 = this.elements[0 * 3 + 1];
    const a02 = this.elements[0 * 3 + 2];
    const a10 = this.elements[1 * 3 + 0];
    const a11 = this.elements[1 * 3 + 1];
    const a12 = this.elements[1 * 3 + 2];
    const a20 = this.elements[2 * 3 + 0];
    const a21 = this.elements[2 * 3 + 1];
    const a22 = this.elements[2 * 3 + 2];
    const b01 = a22 * a11 - a12 * a21;
    const b11 = -a22 * a10 + a12 * a20;
    const b21 = a21 * a10 - a11 * a20;
    const det = a00 * b01 + a01 * b11 + a02 * b21;
    const inv_det = 1 / det;
    return new _Matrix3([
      b01 * inv_det,
      (-a22 * a01 + a02 * a21) * inv_det,
      (a12 * a01 - a02 * a11) * inv_det,
      b11 * inv_det,
      (a22 * a00 - a02 * a20) * inv_det,
      (-a12 * a00 + a02 * a10) * inv_det,
      b21 * inv_det,
      (-a21 * a00 + a01 * a20) * inv_det,
      (a11 * a00 - a01 * a10) * inv_det
    ]);
  }
  /**
   * @returns A new matrix representing a 2D translation
   */
  static translation(x, y) {
    return new _Matrix3([
      1,
      0,
      0,
      0,
      1,
      0,
      x,
      y,
      1
    ]);
  }
  /**
   * Translate this matrix by the given amounts
   * @returns this matrix
   */
  translate_self(x, y) {
    return this.multiply_self(_Matrix3.translation(x, y));
  }
  /**
   * Creates a new matrix representing this matrix translated by the given amount
   * @returns a new matrix
   */
  translate(x, y) {
    return this.copy().translate_self(x, y);
  }
  /**
   * @returns {Matrix3} A new matrix representing a 2D scale
   */
  static scaling(x, y) {
    return new _Matrix3([
      x,
      0,
      0,
      0,
      y,
      0,
      0,
      0,
      1
    ]);
  }
  /**
   * Scale this matrix by the given amounts
   * @returns this matrix
   */
  scale_self(x, y) {
    return this.multiply_self(_Matrix3.scaling(x, y));
  }
  /**
   * Creates a new matrix representing this matrix scaled by the given amount
   * @returns a new matrix
   */
  scale(x, y) {
    return this.copy().scale_self(x, y);
  }
  /**
   * @returns A new matrix representing a 2D rotation
   */
  static rotation(angle) {
    const theta = new Angle(angle).radians;
    const cos = Math.cos(theta);
    const sin = Math.sin(theta);
    return new _Matrix3([
      cos,
      -sin,
      0,
      sin,
      cos,
      0,
      0,
      0,
      1
    ]);
  }
  /**
   * Rotate this matrix by the given angle
   * @returns this matrix
   */
  rotate_self(angle) {
    return this.multiply_self(_Matrix3.rotation(angle));
  }
  /**
   * Creates a new matrix representing this matrix rotated by the given angle
   * @returns a new matrix
   */
  rotate(angle) {
    return this.copy().rotate_self(angle);
  }
  /**
   * Returns the total translation (relative to identity) applied via this matrix.
   */
  get absolute_translation() {
    return this.transform(new Vec2(0, 0));
  }
  /**
   * Retruns the total rotation (relative to identity) applied via this matrix.
   */
  get absolute_rotation() {
    const p0 = this.transform(new Vec2(0, 0));
    const p1 = this.transform(new Vec2(1, 0));
    const pn = p1.sub(p0);
    return pn.angle.normalize();
  }
};

// src/base/math/vec2.ts
var Vec2 = class _Vec2 {
  static {
    __name(this, "Vec2");
  }
  /**
   * Create a Vec2
   */
  constructor(x = 0, y) {
    this.set(x, y);
  }
  /**
   * Copy this vector
   */
  copy() {
    return new _Vec2(...this);
  }
  /**
   * Update this vector's values
   */
  set(x, y) {
    let x_prime = null;
    if (is_number(x) && is_number(y)) {
      x_prime = x;
    } else if (x instanceof _Vec2) {
      x_prime = x.x;
      y = x.y;
    } else if (x instanceof Array) {
      x_prime = x[0];
      y = x[1];
    } else if (x instanceof Object && Object.hasOwn(x, "x")) {
      x_prime = x.x;
      y = x.y;
    } else if (x == 0 && y == void 0) {
      x_prime = 0;
      y = 0;
    }
    if (x_prime == null || y == void 0) {
      throw new Error(`Invalid parameters x: ${x}, y: ${y}.`);
    }
    this.x = x_prime;
    this.y = y;
  }
  /** Iterate through [x, y] */
  *[Symbol.iterator]() {
    yield this.x;
    yield this.y;
  }
  get magnitude() {
    return Math.sqrt(this.x ** 2 + this.y ** 2);
  }
  get squared_magnitude() {
    return this.x ** 2 + this.y ** 2;
  }
  /**
   * @returns the perpendicular normal of this vector
   */
  get normal() {
    return new _Vec2(-this.y, this.x);
  }
  /**
   * @returns the direction (angle) of this vector
   */
  get angle() {
    return new Angle(Math.atan2(this.y, this.x));
  }
  /**
   * KiCad has to be weird about this, ofc.
   */
  get kicad_angle() {
    if (this.x == 0 && this.y == 0) {
      return new Angle(0);
    } else if (this.y == 0) {
      if (this.x >= 0) {
        return new Angle(0);
      } else {
        return Angle.from_degrees(-180);
      }
    } else if (this.x == 0) {
      if (this.y >= 0) {
        return Angle.from_degrees(90);
      } else {
        return Angle.from_degrees(-90);
      }
    } else if (this.x == this.y) {
      if (this.x >= 0) {
        return Angle.from_degrees(45);
      } else {
        return Angle.from_degrees(-135);
      }
    } else if (this.x == -this.y) {
      if (this.x >= 0) {
        return Angle.from_degrees(-45);
      } else {
        return Angle.from_degrees(135);
      }
    } else {
      return this.angle;
    }
  }
  /**
   * @returns A new unit vector in the same direction as this vector
   */
  normalize() {
    if (this.x == 0 && this.y == 0) {
      return new _Vec2(0, 0);
    }
    const l = this.magnitude;
    const x = this.x /= l;
    const y = this.y /= l;
    return new _Vec2(x, y);
  }
  equals(b) {
    return this.x == b?.x && this.y == b?.y;
  }
  add(b) {
    return new _Vec2(this.x + b.x, this.y + b.y);
  }
  sub(b) {
    return new _Vec2(this.x - b.x, this.y - b.y);
  }
  scale(b) {
    return new _Vec2(this.x * b.x, this.y * b.y);
  }
  rotate(angle) {
    const m = Matrix3.rotation(angle);
    return m.transform(this);
  }
  multiply(s) {
    if (is_number(s)) {
      return new _Vec2(this.x * s, this.y * s);
    } else {
      return new _Vec2(this.x * s.x, this.y * s.y);
    }
  }
  resize(len) {
    return this.normalize().multiply(len);
  }
  cross(b) {
    return this.x * b.y - this.y * b.x;
  }
  static segment_intersect(a1, b1, a2, b2) {
    const ray_1 = b1.sub(a1);
    const ray_2 = b2.sub(a2);
    const delta = a2.sub(a1);
    const d = ray_2.cross(ray_1);
    const t1 = ray_2.cross(delta);
    const t2 = ray_1.cross(delta);
    if (d == 0) {
      return null;
    }
    if (d > 0 && (t2 < 0 || t2 > d || t1 < 0 || t1 > d)) {
      return null;
    }
    if (d < 0 && (t2 < d || t1 < d || t1 > 0 || t2 > 0)) {
      return null;
    }
    return new _Vec2(a2.x + t2 / d * ray_2.x, a2.y + t2 / d * ray_2.y);
  }
};

// src/base/math/angle.ts
var Angle = class _Angle {
  static {
    __name(this, "Angle");
  }
  #theta_rad;
  #theta_deg;
  /**
   * Convert radians to degrees
   */
  static rad_to_deg(radians) {
    return radians / Math.PI * 180;
  }
  /**
   * Convert degrees to radians
   */
  static deg_to_rad(degrees) {
    return degrees / 180 * Math.PI;
  }
  /** Round degrees to two decimal places
   *
   * A lot of math involving angles is done with degrees to two decimal places
   * instead of radians to match KiCad's behavior and to avoid floating point
   * nonsense.
   */
  static round(degrees) {
    return Math.round((degrees + Number.EPSILON) * 100) / 100;
  }
  /**
   * Create an Angle
   */
  constructor(radians) {
    if (radians instanceof _Angle) {
      return radians;
    }
    this.radians = radians;
  }
  copy() {
    return new _Angle(this.radians);
  }
  get radians() {
    return this.#theta_rad;
  }
  set radians(v) {
    this.#theta_rad = v;
    this.#theta_deg = _Angle.round(_Angle.rad_to_deg(v));
  }
  get degrees() {
    return this.#theta_deg;
  }
  set degrees(v) {
    this.#theta_deg = v;
    this.#theta_rad = _Angle.deg_to_rad(v);
  }
  static from_degrees(v) {
    return new _Angle(_Angle.deg_to_rad(v));
  }
  /**
   * Returns a new Angle representing the sum of this angle and the given angle.
   */
  add(other) {
    const sum = this.radians + new _Angle(other).radians;
    return new _Angle(sum);
  }
  /**
   * Returns a new Angle representing the difference between this angle and the given angle.
   */
  sub(other) {
    const diff = this.radians - new _Angle(other).radians;
    return new _Angle(diff);
  }
  /**
   * @returns a new Angle constrained to 0 to 360 degrees.
   */
  normalize() {
    let deg = _Angle.round(this.degrees);
    while (deg < 0) {
      deg += 360;
    }
    while (deg >= 360) {
      deg -= 360;
    }
    return _Angle.from_degrees(deg);
  }
  /**
   * @returns a new Angle constrained to -180 to 180 degrees.
   */
  normalize180() {
    let deg = _Angle.round(this.degrees);
    while (deg <= -180) {
      deg += 360;
    }
    while (deg > 180) {
      deg -= 360;
    }
    return _Angle.from_degrees(deg);
  }
  /**
   * @returns a new Angle constrained to -360 to +360 degrees.
   */
  normalize720() {
    let deg = _Angle.round(this.degrees);
    while (deg < -360) {
      deg += 360;
    }
    while (deg >= 360) {
      deg -= 360;
    }
    return _Angle.from_degrees(deg);
  }
  /**
   * @returns a new Angle that's reflected in the other direction, for
   * example, 90 degrees ends up being -90 or 270 degrees (when normalized).
   */
  negative() {
    return new _Angle(-this.radians);
  }
  get is_vertical() {
    return this.degrees == 90 || this.degrees == 270;
  }
  get is_horizontal() {
    return this.degrees == 0 || this.degrees == 180;
  }
  rotate_point(point, origin = new Vec2(0, 0)) {
    let x = point.x - origin.x;
    let y = point.y - origin.y;
    const angle = this.normalize();
    if (angle.degrees == 0) {
    } else if (angle.degrees == 90) {
      [x, y] = [y, -x];
    } else if (angle.degrees == 180) {
      [x, y] = [-x, -y];
    } else if (angle.degrees == 270) {
      [x, y] = [-y, x];
    } else {
      const sina = Math.sin(angle.radians);
      const cosa = Math.cos(angle.radians);
      const [x0, y0] = [x, y];
      x = y0 * sina + x0 * cosa;
      y = y0 * cosa - x0 * sina;
    }
    x += origin.x;
    y += origin.y;
    return new Vec2(x, y);
  }
};

// src/base/math/bbox.ts
var BBox = class _BBox {
  /**
   * Create a bounding box
   */
  constructor(x = 0, y = 0, w = 0, h = 0, context) {
    this.x = x;
    this.y = y;
    this.w = w;
    this.h = h;
    this.context = context;
    if (this.w < 0) {
      this.w *= -1;
      this.x -= this.w;
    }
    if (this.h < 0) {
      this.h *= -1;
      this.y -= this.h;
    }
  }
  static {
    __name(this, "BBox");
  }
  copy() {
    return new _BBox(this.x, this.y, this.w, this.h, this.context);
  }
  /**
   * Create a BBox given the top left and bottom right corners
   */
  static from_corners(x1, y1, x2, y2, context) {
    if (x2 < x1) {
      [x1, x2] = [x2, x1];
    }
    if (y2 < y1) {
      [y1, y2] = [y2, y1];
    }
    return new _BBox(x1, y1, x2 - x1, y2 - y1, context);
  }
  /**
   * Create a BBox that contains all the given points
   */
  static from_points(points, context) {
    if (points.length == 0) {
      return new _BBox(0, 0, 0, 0);
    }
    const first_pt = points[0];
    const start = first_pt.copy();
    const end = first_pt.copy();
    for (const p of points) {
      start.x = Math.min(start.x, p.x);
      start.y = Math.min(start.y, p.y);
      end.x = Math.max(end.x, p.x);
      end.y = Math.max(end.y, p.y);
    }
    return _BBox.from_corners(start.x, start.y, end.x, end.y, context);
  }
  /**
   * Combine two or more BBoxes into a new BBox that contains both
   */
  static combine(boxes, context) {
    let min_x = Number.POSITIVE_INFINITY;
    let min_y = Number.POSITIVE_INFINITY;
    let max_x = Number.NEGATIVE_INFINITY;
    let max_y = Number.NEGATIVE_INFINITY;
    for (const box of boxes) {
      if (!box.valid) {
        continue;
      }
      min_x = Math.min(min_x, box.x);
      min_y = Math.min(min_y, box.y);
      max_x = Math.max(max_x, box.x2);
      max_y = Math.max(max_y, box.y2);
    }
    if (min_x == Number.POSITIVE_INFINITY || min_y == Number.POSITIVE_INFINITY || max_x == Number.NEGATIVE_INFINITY || max_y == Number.NEGATIVE_INFINITY) {
      return new _BBox(0, 0, 0, 0, context);
    }
    return _BBox.from_corners(min_x, min_y, max_x, max_y, context);
  }
  /**
   * @returns true if the BBox has a non-zero area
   */
  get valid() {
    return (this.w !== 0 || this.h !== 0) && this.w !== void 0 && this.h !== void 0;
  }
  get start() {
    return new Vec2(this.x, this.y);
  }
  set start(v) {
    this.x = v.x;
    this.y = v.y;
  }
  get end() {
    return new Vec2(this.x + this.w, this.y + this.h);
  }
  set end(v) {
    this.x2 = v.x;
    this.y2 = v.y;
  }
  get top_left() {
    return this.start;
  }
  get top_right() {
    return new Vec2(this.x + this.w, this.y);
  }
  get bottom_left() {
    return new Vec2(this.x, this.y + this.h);
  }
  get bottom_right() {
    return this.end;
  }
  get x2() {
    return this.x + this.w;
  }
  set x2(v) {
    this.w = v - this.x;
    if (this.w < 0) {
      this.w *= -1;
      this.x -= this.w;
    }
  }
  get y2() {
    return this.y + this.h;
  }
  set y2(v) {
    this.h = v - this.y;
    if (this.h < 0) {
      this.h *= -1;
      this.y -= this.h;
    }
  }
  get center() {
    return new Vec2(this.x + this.w / 2, this.y + this.h / 2);
  }
  /**
   * @returns A new BBox transformed by the given matrix.
   */
  transform(mat) {
    const start = mat.transform(this.start);
    const end = mat.transform(this.end);
    return _BBox.from_corners(start.x, start.y, end.x, end.y, this.context);
  }
  /**
   * @returns A new BBox with the size uniformly modified from the center
   */
  grow(dx, dy) {
    dy ??= dx;
    return new _BBox(
      this.x - dx,
      this.y - dy,
      this.w + dx * 2,
      this.h + dy * 2,
      this.context
    );
  }
  scale(s) {
    return _BBox.from_points(
      [this.start.multiply(s), this.end.multiply(s)],
      this.context
    );
  }
  /**
   * @returns a BBox flipped around the X axis (mirrored Y)
   */
  mirror_vertical() {
    return new _BBox(this.x, -this.y, this.w, -this.h);
  }
  /** returns true if this box contains the other */
  contains(other) {
    return this.contains_point(other.start) && this.contains_point(other.end);
  }
  /**
   * @returns true if the point is within the bounding box.
   */
  contains_point(v) {
    return v.x >= this.x && v.x <= this.x2 && v.y >= this.y && v.y <= this.y2;
  }
  /**
   * @returns A new Vec2 constrained within this bounding box
   */
  constrain_point(v) {
    const x = Math.min(Math.max(v.x, this.x), this.x2);
    const y = Math.min(Math.max(v.y, this.y), this.y2);
    return new Vec2(x, y);
  }
  intersect_segment(a, b) {
    if (this.contains_point(a)) {
      return null;
    }
    const left = [this.top_left, this.bottom_left];
    const right = [this.top_right, this.bottom_right];
    const top = [this.top_left, this.top_right];
    const bottom = [this.bottom_left, this.bottom_right];
    const start = a;
    const end = b;
    for (const seg of [left, right, top, bottom]) {
      const intersection = Vec2.segment_intersect(a, b, ...seg);
      if (!intersection) {
        continue;
      }
      if (intersection.sub(start).squared_magnitude < end.sub(start).squared_magnitude) {
        end.set(intersection);
      }
    }
    if (start.equals(end)) {
      return null;
    }
    return end;
  }
};

// src/base/math/arc.ts
var Arc = class _Arc {
  /**
   * Create a new Arc
   */
  constructor(center, radius, start_angle, end_angle, width, direction = "clockwise") {
    this.center = center;
    this.radius = radius;
    this.start_angle = start_angle;
    this.end_angle = end_angle;
    this.width = width;
    this.direction = direction;
  }
  static {
    __name(this, "Arc");
  }
  /**
   * Create an Arc given three points on a circle
   */
  static from_three_points(start, mid, end, width = 1) {
    const u = 1e6;
    const center = arc_center_from_three_points(
      new Vec2(start.x * u, start.y * u),
      new Vec2(mid.x * u, mid.y * u),
      new Vec2(end.x * u, end.y * u)
    );
    center.x /= u;
    center.y /= u;
    const radius = center.sub(mid).magnitude;
    const start_angle = start.sub(center).angle;
    const mid_angle = mid.sub(center).angle;
    const end_angle = end.sub(center).angle;
    let arc_angle;
    const start_to_mid = mid_angle.sub(start_angle).normalize();
    const start_to_end = end_angle.sub(start_angle).normalize();
    if (start_to_mid.degrees < start_to_end.degrees) {
      arc_angle = start_to_end;
    } else {
      arc_angle = Angle.from_degrees(360).sub(start_to_end);
    }
    let arc_start;
    let direction;
    const mid_to_start = mid.sub(start);
    const end_to_mid = end.sub(mid);
    if (mid_to_start.cross(end_to_mid) < 0) {
      arc_start = end_angle.normalize();
      direction = "counter-clockwise";
    } else {
      arc_start = start_angle.normalize();
      direction = "clockwise";
    }
    const arc_end = arc_start.add(arc_angle);
    return new _Arc(center, radius, arc_start, arc_end, width, direction);
  }
  static from_center_start_end(center, start, end, width) {
    const radius = start.sub(center).magnitude;
    const start_radial = start.sub(center);
    const end_radial = end.sub(center);
    let start_angle = start_radial.kicad_angle;
    let end_angle = end_radial.kicad_angle;
    if (end_angle.degrees == start_angle.degrees) {
      end_angle.degrees = start_angle.degrees + 360;
    }
    if (start_angle.degrees > end_angle.degrees) {
      if (end_angle.degrees < 0) {
        end_angle = end_angle.normalize();
      } else {
        start_angle = start_angle.normalize().sub(Angle.from_degrees(-360));
      }
    }
    return new _Arc(center, radius, start_angle, end_angle, width);
  }
  get start_radial() {
    return this.start_angle.rotate_point(new Vec2(this.radius, 0));
  }
  get start_point() {
    return this.center.add(this.start_radial);
  }
  get end_radial() {
    return this.end_angle.rotate_point(new Vec2(this.radius, 0));
  }
  get end_point() {
    return this.center.add(this.end_radial);
  }
  get mid_angle() {
    return new Angle(
      (this.start_angle.radians + this.end_angle.radians) / 2
    );
  }
  get mid_radial() {
    return this.mid_angle.rotate_point(new Vec2(this.radius, 0));
  }
  get mid_point() {
    return this.center.add(this.mid_radial);
  }
  get arc_angle() {
    return this.end_angle.sub(this.start_angle);
  }
  /**
   * Approximate the Arc using a polyline
   */
  to_polyline() {
    const points = [];
    let start = this.start_angle.radians;
    let end = this.end_angle.radians;
    if (start > end) {
      [end, start] = [start, end];
    }
    for (let theta = start; theta < end; theta += Math.PI / 32) {
      points.push(
        new Vec2(
          this.center.x + Math.cos(theta) * this.radius,
          this.center.y + Math.sin(theta) * this.radius
        )
      );
    }
    let last_angle;
    if (this.direction === "counter-clockwise") {
      points.reverse();
      last_angle = start;
    } else {
      last_angle = end;
    }
    const last_point = new Vec2(
      this.center.x + Math.cos(last_angle) * this.radius,
      this.center.y + Math.sin(last_angle) * this.radius
    );
    if (!last_point.equals(points[points.length - 1])) {
      points.push(last_point);
    }
    return points;
  }
  /**
   * Same as to_polyline, but includes the arc center
   */
  to_polygon() {
    const points = this.to_polyline();
    points.push(this.center);
    return points;
  }
  /**
   * Get a bounding box that encloses the entire arc.
   */
  get bbox() {
    const points = [this.start_point, this.mid_point, this.end_point];
    if (this.start_angle.degrees < 0 && this.end_angle.degrees >= 0) {
      points.push(this.center.add(new Vec2(this.radius, 0)));
    }
    if (this.start_angle.degrees < 90 && this.end_angle.degrees >= 90) {
      points.push(this.center.add(new Vec2(0, this.radius)));
    }
    if (this.start_angle.degrees < 180 && this.end_angle.degrees >= 180) {
      points.push(this.center.add(new Vec2(-this.radius, 0)));
    }
    if (this.start_angle.degrees < 270 && this.end_angle.degrees >= 270) {
      points.push(this.center.add(new Vec2(0, this.radius)));
    }
    if (this.start_angle.degrees < 360 && this.end_angle.degrees >= 360) {
      points.push(this.center.add(new Vec2(0, this.radius)));
    }
    return BBox.from_points(points);
  }
};
function arc_center_from_three_points(start, mid, end) {
  const sqrt_1_2 = Math.SQRT1_2;
  const center = new Vec2(0, 0);
  const y_delta_21 = mid.y - start.y;
  let x_delta_21 = mid.x - start.x;
  const y_delta_32 = end.y - mid.y;
  let x_delta_32 = end.x - mid.x;
  if (x_delta_21 == 0 && y_delta_32 == 0 || y_delta_21 == 0 && x_delta_32 == 0) {
    center.x = (start.x + end.x) / 2;
    center.y = (start.y + end.y) / 2;
    return center;
  }
  if (x_delta_21 == 0) {
    x_delta_21 = Number.EPSILON;
  }
  if (x_delta_32 == 0) x_delta_32 = -Number.EPSILON;
  let slope_a = y_delta_21 / x_delta_21;
  let slope_b = y_delta_32 / x_delta_32;
  const d_slope_a = slope_a * new Vec2(0.5 / y_delta_21, 0.5 / x_delta_21).magnitude;
  const d_slope_b = slope_b * new Vec2(0.5 / y_delta_32, 0.5 / x_delta_32).magnitude;
  if (slope_a == slope_b) {
    if (start == end) {
      center.x = (start.x + mid.x) / 2;
      center.y = (start.y + mid.y) / 2;
      return center;
    } else {
      slope_a += Number.EPSILON;
      slope_b -= Number.EPSILON;
    }
  }
  if (slope_a == 0) {
    slope_a = Number.EPSILON;
  }
  const slope_ab_start_end_y = slope_a * slope_b * (start.y - end.y);
  const d_slope_ab_start_end_y = slope_ab_start_end_y * Math.sqrt(
    d_slope_a / slope_a * d_slope_a / slope_a + d_slope_b / slope_b * d_slope_b / slope_b + sqrt_1_2 / (start.y - end.y) * (sqrt_1_2 / (start.y - end.y))
  );
  const slope_b_start_mid_x = slope_b * (start.x + mid.x);
  const d_slope_b_start_mid_x = slope_b_start_mid_x * Math.sqrt(
    d_slope_b / slope_b * d_slope_b / slope_b + sqrt_1_2 / (start.x + mid.x) * sqrt_1_2 / (start.x + mid.x)
  );
  const slope_a_mid_end_x = slope_a * (mid.x + end.x);
  const d_slope_a_mid_end_x = slope_a_mid_end_x * Math.sqrt(
    d_slope_a / slope_a * d_slope_a / slope_a + sqrt_1_2 / (mid.x + end.x) * sqrt_1_2 / (mid.x + end.x)
  );
  const twice_b_a_slope_diff = 2 * (slope_b - slope_a);
  const d_twice_b_a_slope_diff = 2 * Math.sqrt(d_slope_b * d_slope_b + d_slope_a * d_slope_a);
  const center_numerator_x = slope_ab_start_end_y + slope_b_start_mid_x - slope_a_mid_end_x;
  const d_center_numerator_x = Math.sqrt(
    d_slope_ab_start_end_y * d_slope_ab_start_end_y + d_slope_b_start_mid_x * d_slope_b_start_mid_x + d_slope_a_mid_end_x * d_slope_a_mid_end_x
  );
  const center_x = (slope_ab_start_end_y + slope_b_start_mid_x - slope_a_mid_end_x) / twice_b_a_slope_diff;
  const d_center_x = center_x * Math.sqrt(
    d_center_numerator_x / center_numerator_x * d_center_numerator_x / center_numerator_x + d_twice_b_a_slope_diff / twice_b_a_slope_diff * d_twice_b_a_slope_diff / twice_b_a_slope_diff
  );
  const center_numerator_y = (start.x + mid.x) / 2 - center_x;
  const d_center_numerator_y = Math.sqrt(1 / 8 + d_center_x * d_center_x);
  const center_first_term = center_numerator_y / slope_a;
  const d_center_first_term_y = center_first_term * Math.sqrt(
    d_center_numerator_y / center_numerator_y * d_center_numerator_y / center_numerator_y + d_slope_a / slope_a * d_slope_a / slope_a
  );
  const center_y = center_first_term + (start.y + mid.y) / 2;
  const d_center_y = Math.sqrt(
    d_center_first_term_y * d_center_first_term_y + 1 / 8
  );
  const rounded_100_center_x = Math.floor((center_x + 50) / 100) * 100;
  const rounded_100_center_y = Math.floor((center_y + 50) / 100) * 100;
  const rounded_10_center_x = Math.floor((center_x + 5) / 10) * 10;
  const rounded_10_center_y = Math.floor((center_y + 5) / 10) * 10;
  if (Math.abs(rounded_100_center_x - center_x) < d_center_x && Math.abs(rounded_100_center_y - center_y) < d_center_y) {
    center.x = rounded_100_center_x;
    center.y = rounded_100_center_y;
  } else if (Math.abs(rounded_10_center_x - center_x) < d_center_x && Math.abs(rounded_10_center_y - center_y) < d_center_y) {
    center.x = rounded_10_center_x;
    center.y = rounded_10_center_y;
  } else {
    center.x = center_x;
    center.y = center_y;
  }
  return center;
}
__name(arc_center_from_three_points, "arc_center_from_three_points");

// src/base/array.ts
function as_array(x) {
  if (is_array(x)) {
    return x;
  }
  return [x];
}
__name(as_array, "as_array");
var collator = new Intl.Collator(void 0, { numeric: true });

// src/kicad/tokenizer.ts
var EOF = "";
var Token = class {
  /**
   * Create a new Token
   */
  constructor(type, value = null) {
    this.type = type;
    this.value = value;
  }
  static {
    __name(this, "Token");
  }
  static {
    this.OPEN = /* @__PURE__ */ Symbol("opn");
  }
  static {
    this.CLOSE = /* @__PURE__ */ Symbol("clo");
  }
  static {
    this.ATOM = /* @__PURE__ */ Symbol("atm");
  }
  static {
    this.NUMBER = /* @__PURE__ */ Symbol("num");
  }
  static {
    this.STRING = /* @__PURE__ */ Symbol("str");
  }
};
function is_digit(c) {
  return c >= "0" && c <= "9";
}
__name(is_digit, "is_digit");
function is_alpha(c) {
  return c >= "A" && c <= "Z" || c >= "a" && c <= "z";
}
__name(is_alpha, "is_alpha");
function is_whitespace(c) {
  return c === EOF || c === " " || c === "\n" || c === "\r" || c === "	";
}
__name(is_whitespace, "is_whitespace");
function is_atom(c) {
  return is_alpha(c) || is_digit(c) || [
    "_",
    "-",
    ":",
    "!",
    ".",
    "[",
    "]",
    "{",
    "}",
    "@",
    "*",
    "/",
    "&",
    "#",
    "%",
    "+",
    "=",
    "~",
    "$",
    "|"
  ].includes(c);
}
__name(is_atom, "is_atom");
function error_context(input, index) {
  let start = input.slice(0, index).lastIndexOf("\n");
  if (start < 0) start = 0;
  let end = input.slice(index).indexOf("\n");
  if (end < 0) end = 20;
  return input.slice(start, index + end);
}
__name(error_context, "error_context");
function* tokenize(input) {
  const open_token = new Token(Token.OPEN);
  const close_token = new Token(Token.CLOSE);
  let state = 0 /* none */;
  let start_idx = 0;
  let escaping = false;
  for (let i = 0; i < input.length + 1; i++) {
    const c = i < input.length ? input[i] : EOF;
    if (state == 0 /* none */) {
      if (c === "(") {
        yield open_token;
        continue;
      } else if (c === ")") {
        yield close_token;
        continue;
      } else if (c === '"') {
        state = 1 /* string */;
        start_idx = i;
        continue;
      } else if (c === "-" || c == "+" || is_digit(c)) {
        state = 2 /* number */;
        start_idx = i;
        continue;
      } else if (is_alpha(c) || ["*", "&", "$", "/", "%", "|"].includes(c)) {
        state = 3 /* atom */;
        start_idx = i;
        continue;
      } else if (is_whitespace(c)) {
        continue;
      } else {
        throw new Error(
          `Unexpected character at index ${i}: ${c}
Context: ${error_context(
            input,
            i
          )}`
        );
      }
    } else if (state == 3 /* atom */) {
      if (is_atom(c)) {
        continue;
      } else if (c === ")" || is_whitespace(c)) {
        yield new Token(Token.ATOM, input.substring(start_idx, i));
        state = 0 /* none */;
        if (c === ")") {
          yield close_token;
        }
      } else {
        throw new Error(
          `Unexpected character while tokenizing atom at index ${i}: ${c}
Context: ${error_context(
            input,
            i
          )}`
        );
      }
    } else if (state == 2 /* number */) {
      if (c === "." || is_digit(c)) {
        continue;
      } else if (c.toLowerCase() === "x") {
        state = 4 /* hex */;
        continue;
      } else if (["+", "-", "a", "b", "c", "d", "e", "f"].includes(
        c.toLowerCase()
      )) {
        state = 3 /* atom */;
        continue;
      } else if (is_atom(c)) {
        state = 3 /* atom */;
        continue;
      } else if (c === ")" || is_whitespace(c)) {
        yield new Token(
          Token.NUMBER,
          parseFloat(input.substring(start_idx, i))
        );
        state = 0 /* none */;
        if (c === ")") {
          yield close_token;
        }
        continue;
      } else {
        throw new Error(
          `Unexpected character at index ${i}: ${c}, expected numeric.
Context: ${error_context(
            input,
            i
          )}`
        );
      }
    } else if (state == 4 /* hex */) {
      if (is_digit(c) || ["a", "b", "c", "d", "e", "f", "_"].includes(c.toLowerCase())) {
        continue;
      } else if (c === ")" || is_whitespace(c)) {
        const hexstr = input.substring(start_idx, i).replace("_", "");
        yield new Token(Token.NUMBER, Number.parseInt(hexstr, 16));
        state = 0 /* none */;
        if (c === ")") {
          yield close_token;
        }
        continue;
      } else if (is_atom(c)) {
        state = 3 /* atom */;
        continue;
      } else {
        throw new Error(
          `Unexpected character at index ${i}: ${c}, expected hexadecimal.
Context: ${error_context(
            input,
            i
          )}`
        );
      }
    } else if (state == 1 /* string */) {
      if (!escaping && c === '"') {
        yield new Token(
          Token.STRING,
          input.substring((start_idx ?? 0) + 1, i).replaceAll("\\n", "\n").replaceAll("\\\\", "\\")
        );
        state = 0 /* none */;
        escaping = false;
        continue;
      } else if (!escaping && c === "\\") {
        escaping = true;
        continue;
      } else {
        escaping = false;
        continue;
      }
    } else {
      throw new Error(
        `Unknown tokenizer state ${state}
Context: ${error_context(
          input,
          i
        )}`
      );
    }
  }
}
__name(tokenize, "tokenize");
function* listify_tokens(tokens) {
  let token;
  let it;
  while (true) {
    it = tokens.next();
    token = it.value;
    switch (token?.type) {
      case Token.ATOM:
      case Token.STRING:
      case Token.NUMBER:
        yield token.value;
        break;
      case Token.OPEN:
        yield Array.from(listify_tokens(tokens));
        break;
      case Token.CLOSE:
      case void 0:
        return;
    }
  }
}
__name(listify_tokens, "listify_tokens");
function listify(src) {
  const tokens = tokenize(src);
  return Array.from(listify_tokens(tokens));
}
__name(listify, "listify");

// src/kicad/parser.ts
var log = new Logger("kicanvas:parser");
var T = {
  any(obj, name, e) {
    return e;
  },
  boolean(obj, name, e) {
    switch (e) {
      case "false":
      case "no":
        return false;
      case "true":
      case "yes":
        return true;
      default:
        return e ? true : false;
    }
  },
  string(obj, name, e) {
    if (is_string(e)) {
      return e;
    } else {
      return void 0;
    }
  },
  number(obj, name, e) {
    if (is_number(e)) {
      return e;
    } else {
      return void 0;
    }
  },
  item(type, ...args) {
    return (obj, name, e) => {
      return new type(e, ...args);
    };
  },
  object(start, ...defs) {
    return (obj, name, e) => {
      let existing = {};
      if (start !== null) {
        existing = obj[name] ?? start ?? {};
      }
      return {
        ...existing,
        ...parse_expr(e, P.start(name), ...defs)
      };
    };
  },
  vec2(obj, name, e) {
    const el = e;
    return new Vec2(el[1], el[2]);
  },
  color(obj, name, e) {
    const el = e;
    return new Color(el[1] / 255, el[2] / 255, el[3] / 255, el[4]);
  },
  /**
   * Choose a type processor by prefix
   *
   * Example: `choice[("xy", T.vec2), ("color", T.color)]`
   *  - if input is `(xy 1 2)`, use `T.vec2` to parse input
   *  - if input is `(color 1 2 3)`, use `T.color` to parse input
   */
  choice(...fns) {
    return (obj, name, e) => {
      const e_str = e;
      for (const [prefix, fn] of fns) {
        if (prefix === e_str[0]) {
          return fn(obj, name, e);
        }
      }
      throw new Error(`No matched for ${e}`);
    };
  }
};
var P = {
  /**
   * Checks that the first item in the list is "name". For example,
   * (thing 1 2 3) would use start("thing").
   */
  start(name) {
    return {
      kind: 0 /* start */,
      name,
      fn: T.string
    };
  },
  /**
   * Accepts a positional argument. For example,
   * (1 2 3) with positional("first", T.number) would end up with {first: 1}.
   */
  positional(name, typefn = T.any) {
    return {
      kind: 1 /* positional */,
      name,
      fn: typefn
    };
  },
  /**
   * Accepts a pair. For example, ((a 1)) with pair(a) would end up with {a: 1}.
   */
  pair(name, typefn = T.any) {
    return {
      kind: 2 /* pair */,
      name,
      accepts: [name],
      fn: /* @__PURE__ */ __name((obj, name2, e) => {
        return typefn(obj, name2, e[1]);
      }, "fn")
    };
  },
  /**
   * Accepts a list. For example ((a 1 2 3)) with list(a) would end up with {a: [1, 2, 3]}.
   */
  list(name, typefn = T.any) {
    return {
      kind: 3 /* list */,
      name,
      accepts: [name],
      fn: /* @__PURE__ */ __name((obj, name2, e) => {
        return e.slice(1).map((n) => typefn(obj, name2, n));
      }, "fn")
    };
  },
  /**
   * Accepts a collection. For example ((a 1) (a 2) (a 3)) with collection("items", "a")
   * would end up with {items: [[a, 1], [a, 2], [a, 3]]}.
   */
  collection(name, accept, typefn = T.any) {
    return {
      kind: 5 /* item_list */,
      name,
      accepts: [accept],
      fn: /* @__PURE__ */ __name((obj, name2, e) => {
        const list = obj[name2] ?? [];
        list.push(typefn(obj, name2, e));
        return list;
      }, "fn")
    };
  },
  /**
   * Like collection but creates a map instead of an array.. For example
   * ((a key1 1) (a key2 2) (a key3 3)) with collection_map("items", "a")
   * would end up with {items: {key1: [a, key1, 2], ...}.
   */
  mapped_collection(name, accept, keyfn, typefn = T.any) {
    return {
      kind: 5 /* item_list */,
      name,
      accepts: [accept],
      fn: /* @__PURE__ */ __name((obj, name2, e) => {
        const map = obj[name2] ?? /* @__PURE__ */ new Map();
        const val = typefn(obj, name2, e);
        const key = keyfn(val);
        map.set(key, val);
        return map;
      }, "fn")
    };
  },
  /**
   * Accepts a dictionary. For example ((thing a 1) (thing b 2) (thing c 3)) with
   * dict("things", "thing") would end up with {things: {a: 1, b: 2, c: 3}}.
   */
  dict(name, accept, typefn = T.any) {
    return {
      kind: 5 /* item_list */,
      name,
      accepts: [accept],
      fn: /* @__PURE__ */ __name((obj, name2, e) => {
        const el = e;
        const rec = obj[name2] ?? {};
        rec[el[1]] = typefn(obj, name2, el.slice(2));
        return rec;
      }, "fn")
    };
  },
  /**
   * Accepts an atom. For example (locked) and ((locked)) with atom("locked")
   * would end up with {locked: true}. Atoms can also be mutually exclusive
   * options, for example atom("align", ["left", "right"]) would process
   * (left) as {align: "left"} and (right) as {align: "right"}.
   */
  atom(name, values) {
    let typefn;
    if (values) {
      typefn = T.string;
    } else {
      typefn = T.boolean;
      values = [name];
    }
    return {
      kind: 4 /* atom */,
      name,
      accepts: values,
      fn(obj, name2, e) {
        if (Array.isArray(e) && e.length == 1) {
          e = e[0];
        }
        return typefn(obj, name2, e);
      }
    };
  },
  /**
   * Accepts an expression. For example ((thing a 1 b)) with expr("thing")
   * would end up with {thing: ["thing", a, 1, b]}.
   */
  expr(name, typefn = T.any) {
    return {
      kind: 6 /* expr */,
      name,
      accepts: [name],
      fn: typefn
    };
  },
  /**
   * Accepts an expression that describes a simple object with the given
   * property definitions. For example ((thing (a 1) (b 2))) with
   * object("thing", P.pair("a"), P.pair("b")) would end up with
   * {thing: {a: 1, b: 2}}.
   */
  object(name, start, ...defs) {
    return P.expr(name, T.object(start, ...defs));
  },
  /**
   * Accepts an expression that describes an object that can be used to
   * construct the given Item type. An Item is any class that takes
   * a List as its first constructor parameter.
   */
  item(name, item_type, ...args) {
    return P.expr(name, T.item(item_type, ...args));
  },
  /**
   * Accepts an expression that describes a 2D vector. For example,
   * ((xy 1 2)) with vec2("xy") would end up with {xy: Vec2(1, 2)}.
   */
  vec2(name) {
    return P.expr(name, T.vec2);
  },
  color(name = "color") {
    return P.expr(name, T.color);
  }
};
function parse_expr(expr, ...defs) {
  if (is_string(expr)) {
    log.info(`Parsing expression with ${expr.length} chars`);
    expr = listify(expr);
    if (expr.length == 1 && Array.isArray(expr[0])) {
      expr = expr[0];
    }
  }
  const defs_map = /* @__PURE__ */ new Map();
  let start_def;
  let n = 0;
  for (const def of defs) {
    if (def.kind == 0 /* start */) {
      start_def = def;
    } else if (def.kind == 1 /* positional */) {
      defs_map.set(n, def);
      n++;
    } else {
      for (const a of def.accepts) {
        defs_map.set(a, def);
      }
    }
  }
  if (start_def) {
    const acceptable_start_strings = as_array(start_def.name);
    const first = expr.at(0);
    if (!acceptable_start_strings.includes(first)) {
      throw new Error(
        `Expression must start with ${start_def.name}, but found ${first} in ${expr}`
      );
    }
    expr = expr.slice(1);
  }
  const out = {};
  n = 0;
  for (const element of expr) {
    let def = null;
    if (is_string(element)) {
      def = defs_map.get(element);
    }
    if (!def && (is_string(element) || is_number(element))) {
      def = defs_map.get(n);
      if (!def) {
        log.warn(
          `Bare element ${element} is undefined at position ${n} in expression ${expr}`
        );
        continue;
      }
      n++;
    }
    if (!def && Array.isArray(element)) {
      def = defs_map.get(element[0]);
    }
    if (!def) {
      log.warn(
        `No definition found for element ${element} in expression ${expr}`
      );
      continue;
    }
    const value = def.fn(out, def.name, element);
    out[def.name] = value;
  }
  return out;
}
__name(parse_expr, "parse_expr");

// src/kicad/common.ts
function unescape_string(str) {
  const escape_vars = {
    dblquote: '"',
    quote: "'",
    lt: "<",
    gt: ">",
    backslash: "\\",
    slash: "/",
    bar: "|",
    comma: ",",
    colon: ":",
    space: " ",
    dollar: "$",
    tab: "	",
    return: "\n",
    brace: "{"
  };
  for (const [k, v] of Object.entries(escape_vars)) {
    str = str.replaceAll("{" + k + "}", v);
  }
  return str;
}
__name(unescape_string, "unescape_string");
function expand_text_vars(text, resolveable) {
  text = unescape_string(text);
  if (resolveable === void 0) {
    return text;
  }
  let last_len;
  let retry_count = 8;
  const resolved = [];
  do {
    last_len = resolved.length;
    retry_count -= 1;
    text = text.replaceAll(
      /(\$\{(.+?)\})/g,
      (substring, all, name) => {
        if (resolved.includes(name)) {
          log.warn(`Cycle reference "${name}" in text "${text}"`);
          return all;
        }
        const val = resolveable.resolve_text_var(name);
        if (val === void 0) {
          return all;
        }
        resolved.push(name);
        return val;
      }
    );
  } while (last_len !== resolved.length || retry_count === 0);
  return text;
}
__name(expand_text_vars, "expand_text_vars");
var At = class _At {
  constructor(expr) {
    this.position = new Vec2(0, 0);
    this.rotation = 0;
    this.unlocked = false;
    if (expr) {
      const parsed = parse_expr(
        expr,
        P.start("at"),
        P.positional("x", T.number),
        P.positional("y", T.number),
        P.positional("rotation", T.number),
        P.atom("unlocked")
      );
      this.position.set(parsed.x, parsed.y);
      this.rotation = parsed.rotation ?? this.rotation;
      this.unlocked = parsed.unlocked ?? this.unlocked;
    }
  }
  static {
    __name(this, "At");
  }
  copy() {
    const at = new _At();
    at.position = this.position.copy();
    at.rotation = this.rotation;
    at.unlocked = this.unlocked;
    return at;
  }
};
var PaperSize = {
  User: [431.8, 279.4],
  A0: [1189, 841],
  A1: [841, 594],
  A2: [594, 420],
  A3: [420, 297],
  A4: [297, 210],
  A5: [210, 148],
  A: [279.4, 215.9],
  B: [431.8, 279.4],
  C: [558.8, 431.8],
  D: [863.6, 558.8],
  E: [1117.6, 863.6],
  USLetter: [279.4, 215.9],
  USLegal: [355.6, 215.9],
  USLedger: [431.8, 279.4]
};
var Paper = class {
  constructor(expr) {
    this.portrait = false;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("paper"),
        P.atom("size", Object.keys(PaperSize)),
        P.positional("width", T.number),
        P.positional("height", T.number),
        P.atom("portrait")
      )
    );
    const paper_size = PaperSize[this.size];
    if (!this.width && paper_size) {
      this.width = paper_size[0];
    }
    if (!this.height && paper_size) {
      this.height = paper_size[1];
    }
    if (this.size != "User" && this.portrait) {
      [this.width, this.height] = [this.height, this.width];
    }
  }
  static {
    __name(this, "Paper");
  }
};
var TitleBlock = class {
  constructor(expr) {
    this.title = "";
    this.date = "";
    this.rev = "";
    this.company = "";
    this.comment = {};
    if (expr) {
      Object.assign(
        this,
        parse_expr(
          expr,
          P.start("title_block"),
          P.pair("title", T.string),
          P.pair("date", T.string),
          P.pair("rev", T.string),
          P.pair("company", T.string),
          P.expr("comment", (obj, name, e) => {
            const ep = e;
            const record = obj[name] ?? {};
            record[ep[1]] = ep[2];
            return record;
          })
        )
      );
    }
  }
  static {
    __name(this, "TitleBlock");
  }
  resolve_text_var(name) {
    return (/* @__PURE__ */ new Map([
      ["ISSUE_DATE", this.date],
      ["REVISION", this.rev],
      ["TITLE", this.title],
      ["COMPANY", this.company],
      ["COMMENT1", this.comment[1] ?? ""],
      ["COMMENT2", this.comment[2] ?? ""],
      ["COMMENT3", this.comment[3] ?? ""],
      ["COMMENT4", this.comment[4] ?? ""],
      ["COMMENT5", this.comment[5] ?? ""],
      ["COMMENT6", this.comment[6] ?? ""],
      ["COMMENT7", this.comment[7] ?? ""],
      ["COMMENT8", this.comment[8] ?? ""],
      ["COMMENT9", this.comment[9] ?? ""]
    ])).get(name);
  }
};
var Effects = class _Effects {
  constructor(expr) {
    this.font = new Font();
    this.justify = new Justify();
    this.hide = false;
    if (expr) {
      Object.assign(
        this,
        parse_expr(
          expr,
          P.start("effects"),
          P.item("font", Font),
          P.item("justify", Justify),
          P.atom("hide"),
          P.color()
        )
      );
    }
  }
  static {
    __name(this, "Effects");
  }
  copy() {
    const e = new _Effects();
    e.font = this.font.copy();
    e.justify = this.justify.copy();
    e.hide = this.hide;
    return e;
  }
};
var Font = class _Font {
  constructor(expr) {
    this.size = new Vec2(1.27, 1.27);
    this.thickness = 0;
    this.bold = false;
    this.italic = false;
    this.color = Color.transparent_black;
    if (expr) {
      Object.assign(
        this,
        parse_expr(
          expr,
          P.start("font"),
          P.pair("face", T.string),
          P.vec2("size"),
          P.pair("thickness", T.number),
          P.atom("bold"),
          P.atom("italic"),
          P.pair("line_spacing", T.number),
          P.color()
        )
      );
      [this.size.x, this.size.y] = [this.size.y, this.size.x];
    }
  }
  static {
    __name(this, "Font");
  }
  copy() {
    const f = new _Font();
    f.face = this.face;
    f.size = this.size.copy();
    f.thickness = this.thickness;
    f.bold = this.bold;
    f.italic = this.italic;
    return f;
  }
};
var Justify = class _Justify {
  constructor(expr) {
    this.horizontal = "center";
    this.vertical = "center";
    this.mirror = false;
    if (expr) {
      Object.assign(
        this,
        parse_expr(
          expr,
          P.start("justify"),
          P.atom("horizontal", ["left", "right"]),
          P.atom("vertical", ["top", "bottom"]),
          P.atom("mirror")
        )
      );
    }
  }
  static {
    __name(this, "Justify");
  }
  copy() {
    const j = new _Justify();
    j.horizontal = this.horizontal;
    j.vertical = this.vertical;
    j.mirror = this.mirror;
    return j;
  }
};
var Stroke = class {
  constructor(expr) {
    this.type = "default";
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("stroke"),
        P.pair("width", T.number),
        P.pair("type", T.string),
        P.color()
      )
    );
  }
  static {
    __name(this, "Stroke");
  }
  static default_value() {
    return {
      width: 0,
      type: "default",
      color: Color.transparent_black
    };
  }
};
var EmbeddedFile = class {
  static {
    __name(this, "EmbeddedFile");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("file"),
        P.pair("name", T.string),
        P.pair("type", T.string),
        P.pair("data", T.string),
        P.pair("checksum", T.string)
      )
    );
  }
  async decompress_file() {
    if (!this.data) {
      return void 0;
    }
    return void 0;
  }
};
var StrokeParams = class _StrokeParams {
  static {
    __name(this, "StrokeParams");
  }
  static {
    /** ISO 128-2 line correction factor */
    this.line_correction = 1;
  }
  /** Calculate the length of a dot in a dashed line. */
  static dot_length(line) {
    return Math.max(1 - _StrokeParams.line_correction, 0.2) * line;
  }
  /** Calculate the length of a gap in a dashed line. */
  static gap_length(line, stroke) {
    const gap_ratio = stroke.dashed_line_gap_ratio;
    return Math.max(gap_ratio + _StrokeParams.line_correction, 1) * line;
  }
  /** Calculate the length of a dash in a dashed line. */
  static dash_length(line, stroke) {
    const dash_ratio = stroke.dashed_line_dash_ratio;
    return Math.max(dash_ratio - _StrokeParams.line_correction, 1) * line;
  }
  /** Solid line, gap: 3, dash: 12. */
  static default_value() {
    return {
      stroke: Stroke.default_value(),
      dashed_line_gap_ratio: 3,
      dashed_line_dash_ratio: 12
    };
  }
};
var Net = class {
  static {
    __name(this, "Net");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("net"),
        P.positional("number", T.number),
        P.positional("name", T.string)
      )
    );
  }
};

// src/kicad/schematic.ts
var DefaultValues = {
  /* The size of the rectangle indicating an unconnected wire or label */
  dangling_symbol_size: 0.3048,
  // 12 mils
  /* The size of the rectangle indicating a connected, unselected wire end */
  unselected_end_size: 0.1016,
  // 4 mils
  pin_length: 2.54,
  // 100 mils
  pinsymbol_size: 0.635,
  // 25 mils
  pinnum_size: 1.27,
  // 50 mils
  pinname_size: 1.27,
  // 50 mils
  selection_thickness: 0.0762,
  // 3 mils
  line_width: 0.1524,
  // 6 mils
  wire_width: 0.1524,
  // 6 mils
  bus_width: 0.3048,
  // 12 mils
  noconnect_size: 1.2192,
  // 48 mils
  junction_diameter: 0.9144,
  // 36 mils
  target_pin_radius: 0.381,
  // 15 mils
  /* The default bus and wire entry size. */
  sch_entry_size: 2.54,
  // 100 mils
  text_size: 1.27,
  // 50 mils
  /* Ratio of the font height to the baseline of the text above the wire. */
  text_offset_ratio: 0.15,
  // unitless ratio
  /* Ratio of the font height to space around global labels */
  label_size_ratio: 0.375,
  // unitless ratio
  /* The offset of the pin name string from the end of the pin in mils. */
  pin_name_offset: 0.508
  // 20 mils
};
var KicadSch = class {
  constructor(filename, expr) {
    this.filename = filename;
    this.title_block = new TitleBlock();
    this.wires = [];
    this.buses = [];
    this.bus_entries = [];
    this.bus_aliases = [];
    this.junctions = [];
    this.net_labels = [];
    this.global_labels = [];
    this.hierarchical_labels = [];
    this.symbols = /* @__PURE__ */ new Map();
    this.no_connects = [];
    this.drawings = [];
    this.rule_areas = [];
    this.netclass_flags = [];
    this.images = [];
    this.sheets = [];
    this.embedded_fonts = false;
    this.embedded_files = [];
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("kicad_sch"),
        P.pair("version", T.number),
        P.pair("generator", T.string),
        P.pair("generator_version", T.string),
        P.pair("uuid", T.string),
        P.item("paper", Paper),
        P.item("title_block", TitleBlock),
        P.item("lib_symbols", LibSymbols, this),
        P.collection("wires", "wire", T.item(Wire)),
        P.collection("buses", "bus", T.item(Bus)),
        P.collection("bus_entries", "bus_entry", T.item(BusEntry)),
        P.collection("bus_aliases", "bus_alias", T.item(BusAlias)),
        P.collection("junctions", "junction", T.item(Junction)),
        P.collection("no_connects", "no_connect", T.item(NoConnect)),
        P.collection("net_labels", "label", T.item(NetLabel)),
        P.collection(
          "global_labels",
          "global_label",
          T.item(GlobalLabel, this)
        ),
        P.collection(
          "hierarchical_labels",
          "hierarchical_label",
          T.item(HierarchicalLabel, this)
        ),
        P.mapped_collection(
          "symbols",
          "symbol",
          (p) => p.uuid,
          T.item(SchematicSymbol, this)
        ),
        P.collection("drawings", "polyline", T.item(Polyline, this)),
        P.collection("drawings", "rectangle", T.item(Rectangle, this)),
        P.collection("drawings", "arc", T.item(Arc2, this)),
        P.collection("drawings", "text", T.item(Text, this)),
        P.collection("drawings", "circle", T.item(Circle, this)),
        P.collection("rule_areas", "rule_area", T.item(RuleArea, this)),
        P.collection(
          "netclass_flags",
          "netclass_flag",
          T.item(DirectiveLabel, this)
        ),
        P.collection("images", "image", T.item(Image)),
        P.item("sheet_instances", SheetInstances),
        P.item("symbol_instances", SymbolInstances),
        P.collection("sheets", "sheet", T.item(SchematicSheet, this)),
        P.pair("embedded_fonts", T.boolean),
        P.list("embedded_files", T.item(EmbeddedFile))
      )
    );
    this.update_hierarchical_data();
  }
  static {
    __name(this, "KicadSch");
  }
  update_hierarchical_data(path) {
    path ??= ``;
    const root_symbol_instances = this.project?.root_schematic_page?.document?.symbol_instances;
    const global_symbol_instances = this.symbol_instances;
    for (const s of this.symbols.values()) {
      const symbol_path = `${path}/${s.uuid}`;
      const instance_data = root_symbol_instances?.get(symbol_path) ?? global_symbol_instances?.get(symbol_path) ?? s.instances.get(path);
      if (!instance_data) {
        continue;
      }
      s.reference = instance_data.reference ?? s.reference;
      s.value = instance_data.value ?? s.value;
      s.footprint = instance_data.footprint ?? s.footprint;
      s.unit = instance_data.unit ?? s.unit;
    }
    const root_sheet_instances = this.project?.root_schematic_page?.document?.sheet_instances;
    const global_sheet_instances = this.sheet_instances;
    for (const s of this.sheets) {
      const sheet_path = `${path}/${s.uuid}`;
      const instance_data = root_sheet_instances?.get(sheet_path) ?? global_sheet_instances?.get(sheet_path) ?? s.instances.get(path);
      if (!instance_data) {
        continue;
      }
      s.page = instance_data.page;
      s.path = instance_data.path;
      if (!s.instances.size) {
        const inst = new SchematicSheetInstance();
        inst.page = instance_data.page;
        inst.path = instance_data.path;
        s.instances.set("", inst);
      }
    }
  }
  *items() {
    yield* this.wires;
    yield* this.buses;
    yield* this.bus_entries;
    yield* this.junctions;
    yield* this.net_labels;
    yield* this.global_labels;
    yield* this.netclass_flags;
    yield* this.hierarchical_labels;
    yield* this.no_connects;
    yield* this.symbols.values();
    yield* this.drawings;
    yield* this.rule_areas;
    yield* this.images;
    yield* this.sheets;
  }
  find_symbol(uuid_or_ref) {
    if (this.symbols.has(uuid_or_ref)) {
      return this.symbols.get(uuid_or_ref);
    }
    for (const sym of this.symbols.values()) {
      if (sym.uuid == uuid_or_ref || sym.reference == uuid_or_ref) {
        return sym;
      }
    }
    return null;
  }
  find_sheet(uuid) {
    for (const sheet of this.sheets) {
      if (sheet.uuid == uuid) {
        return sheet;
      }
    }
    return null;
  }
  resolve_text_var(name) {
    if (name == "FILENAME") {
      return this.filename;
    }
    if (name.includes(":")) {
      const [uuid, field_name] = name.split(":");
      const symbol = this.symbols.get(uuid);
      if (symbol) {
        return symbol.resolve_text_var(field_name);
      }
    }
    return this.title_block.resolve_text_var(name);
  }
};
var Fill = class {
  static {
    __name(this, "Fill");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("fill"),
        P.pair("type", T.string),
        P.color()
      )
    );
  }
};
var GraphicItem = class {
  constructor(parent) {
    this.private = false;
    this.parent = parent;
  }
  static {
    __name(this, "GraphicItem");
  }
  get stroke_params() {
    return {
      stroke: this.stroke ?? Stroke.default_value(),
      dashed_line_gap_ratio: 3,
      dashed_line_dash_ratio: 12
    };
  }
  static {
    this.common_expr_defs = [
      P.atom("private"),
      P.item("stroke", Stroke),
      P.item("fill", Fill),
      P.pair("uuid", T.string)
    ];
  }
};
var Wire = class {
  static {
    __name(this, "Wire");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("wire"),
        P.list("pts", T.vec2),
        P.item("stroke", Stroke),
        P.pair("uuid", T.string)
      )
    );
  }
};
var Bus = class {
  static {
    __name(this, "Bus");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("bus"),
        P.list("pts", T.vec2),
        P.item("stroke", Stroke),
        P.pair("uuid", T.string)
      )
    );
  }
};
var BusEntry = class {
  static {
    __name(this, "BusEntry");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("bus_entry"),
        P.item("at", At),
        P.vec2("size"),
        P.item("stroke", Stroke),
        P.pair("uuid", T.string)
      )
    );
  }
};
var BusAlias = class {
  constructor(expr) {
    this.members = [];
    Object.assign(
      this,
      parse_expr(expr, P.start("bus_alias"), P.list("members", T.string))
    );
  }
  static {
    __name(this, "BusAlias");
  }
};
var Junction = class {
  static {
    __name(this, "Junction");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("junction"),
        P.item("at", At),
        P.pair("diameter", T.number),
        P.color(),
        P.pair("uuid", T.string)
      )
    );
  }
};
var NoConnect = class {
  static {
    __name(this, "NoConnect");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("no_connect"),
        P.item("at", At),
        P.pair("uuid", T.string)
      )
    );
  }
};
var Arc2 = class extends GraphicItem {
  static {
    __name(this, "Arc");
  }
  constructor(expr, parent) {
    super(parent);
    const parsed = parse_expr(
      expr,
      P.start("arc"),
      P.vec2("start"),
      P.vec2("mid"),
      P.vec2("end"),
      P.object(
        "radius",
        {},
        P.start("radius"),
        P.vec2("at"),
        P.pair("length"),
        P.vec2("angles")
      ),
      ...GraphicItem.common_expr_defs
    );
    if (parsed["radius"]?.["length"]) {
      const arc = Arc.from_center_start_end(
        parsed["radius"]["at"],
        parsed["end"],
        parsed["start"],
        1
      );
      if (arc.arc_angle.degrees > 180) {
        [arc.start_angle, arc.end_angle] = [
          arc.end_angle,
          arc.start_angle
        ];
      }
      parsed["start"] = arc.start_point;
      parsed["mid"] = arc.mid_point;
      parsed["end"] = arc.end_point;
    }
    delete parsed["radius"];
    Object.assign(this, parsed);
  }
};
var Bezier = class extends GraphicItem {
  static {
    __name(this, "Bezier");
  }
  constructor(expr, parent) {
    super(parent);
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("bezier"),
        P.list("pts", T.vec2),
        ...GraphicItem.common_expr_defs
      )
    );
  }
  get start() {
    return this.pts[0];
  }
  get c1() {
    return this.pts[1];
  }
  get c2() {
    return this.pts[2];
  }
  get end() {
    return this.pts[3];
  }
};
var Circle = class extends GraphicItem {
  static {
    __name(this, "Circle");
  }
  constructor(expr, parent) {
    super(parent);
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("circle"),
        P.vec2("center"),
        P.pair("radius", T.number),
        ...GraphicItem.common_expr_defs
      )
    );
  }
};
var Polyline = class extends GraphicItem {
  static {
    __name(this, "Polyline");
  }
  constructor(expr, parent) {
    super(parent);
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("polyline"),
        P.list("pts", T.vec2),
        ...GraphicItem.common_expr_defs
      )
    );
  }
};
var Rectangle = class extends GraphicItem {
  static {
    __name(this, "Rectangle");
  }
  constructor(expr, parent) {
    super(parent);
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("rectangle"),
        P.vec2("start"),
        P.vec2("end"),
        ...GraphicItem.common_expr_defs
      )
    );
  }
};
var Image = class {
  static {
    __name(this, "Image");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("image"),
        P.item("at", At),
        P.pair("data", T.string),
        P.pair("uuid", T.string)
      )
    );
  }
};
var Text = class {
  constructor(expr, parent) {
    this.parent = parent;
    this.private = false;
    this.effects = new Effects();
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("text"),
        P.positional("text"),
        P.item("at", At),
        P.item("effects", Effects),
        P.pair("uuid", T.string)
      )
    );
    if (this.text.endsWith("\n")) {
      this.text = this.text.slice(0, this.text.length - 1);
    }
  }
  static {
    __name(this, "Text");
  }
  get shown_text() {
    return expand_text_vars(this.text, this.parent);
  }
};
var LibText = class extends Text {
  constructor(expr, parent) {
    super(expr, parent);
    this.parent = parent;
    if (parent instanceof LibSymbol || parent instanceof SchematicSymbol) {
      this.at.rotation /= 10;
    }
  }
  static {
    __name(this, "LibText");
  }
};
var TextBox = class extends GraphicItem {
  constructor(expr, parent) {
    super(parent);
    this.effects = new Effects();
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("text"),
        P.positional("text"),
        P.item("at", At),
        P.vec2("size"),
        P.item("effects", Effects),
        ...GraphicItem.common_expr_defs
      )
    );
  }
  static {
    __name(this, "TextBox");
  }
};
var Label = class {
  constructor() {
    this.private = false;
    this.at = new At();
    this.effects = new Effects();
    this.fields_autoplaced = false;
  }
  static {
    __name(this, "Label");
  }
  static {
    this.common_expr_defs = [
      P.positional("text"),
      P.item("at", At),
      P.item("effects", Effects),
      P.atom("fields_autoplaced"),
      P.pair("uuid", T.string)
    ];
  }
  get shown_text() {
    return unescape_string(this.text);
  }
};
var NetLabel = class extends Label {
  static {
    __name(this, "NetLabel");
  }
  constructor(expr) {
    super();
    Object.assign(
      this,
      parse_expr(expr, P.start("label"), ...Label.common_expr_defs)
    );
  }
};
var GlobalLabel = class extends Label {
  constructor(expr) {
    super();
    this.shape = "input";
    this.properties = [];
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("global_label"),
        ...Label.common_expr_defs,
        P.pair("shape", T.string),
        P.collection("properties", "property", T.item(Property))
      )
    );
  }
  static {
    __name(this, "GlobalLabel");
  }
};
var HierarchicalLabel = class extends Label {
  constructor(expr) {
    super();
    this.shape = "input";
    if (expr) {
      Object.assign(
        this,
        parse_expr(
          expr,
          P.start("hierarchical_label"),
          ...Label.common_expr_defs,
          P.pair("shape", T.string)
        )
      );
    }
  }
  static {
    __name(this, "HierarchicalLabel");
  }
};
var DirectiveLabel = class extends Label {
  constructor(expr, parent) {
    super();
    this.parent = parent;
    this.properties = [];
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("netclass_flag"),
        ...Label.common_expr_defs,
        P.pair("shape", T.string),
        P.pair("length", T.number),
        P.collection("properties", "property", T.item(Property, this))
      )
    );
  }
  static {
    __name(this, "DirectiveLabel");
  }
};
var LibSymbols = class {
  constructor(expr, parent) {
    this.parent = parent;
    this.symbols = [];
    this.#symbols_by_name = /* @__PURE__ */ new Map();
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("lib_symbols"),
        P.collection("symbols", "symbol", T.item(LibSymbol, parent))
      )
    );
    for (const symbol of this.symbols) {
      this.#symbols_by_name.set(symbol.name, symbol);
    }
  }
  static {
    __name(this, "LibSymbols");
  }
  #symbols_by_name;
  by_name(name) {
    return this.#symbols_by_name.get(name);
  }
};
var LibSymbol = class _LibSymbol {
  constructor(expr, parent) {
    this.parent = parent;
    this.power = false;
    this.pin_numbers = { hide: false };
    this.pin_names = {
      offset: DefaultValues.pin_name_offset,
      hide: false
    };
    this.in_bom = false;
    this.on_board = false;
    this.in_pos_files = true;
    this.exclude_from_sim = false;
    this.duplicate_pin_numbers_are_jumpers = false;
    this.properties = /* @__PURE__ */ new Map();
    this.children = [];
    this.drawings = [];
    this.pins = [];
    this.units = /* @__PURE__ */ new Map();
    this.embedded_fonts = false;
    this.embedded_files = [];
    this.#pins_by_number = /* @__PURE__ */ new Map();
    this.#properties_by_id = /* @__PURE__ */ new Map();
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("symbol"),
        P.positional("name"),
        P.atom("power"),
        P.object("pin_numbers", this.pin_numbers, P.atom("hide")),
        P.object(
          "pin_names",
          this.pin_names,
          P.pair("offset", T.number),
          P.atom("hide")
        ),
        P.pair("in_bom", T.boolean),
        P.pair("on_board", T.boolean),
        P.pair("in_pos_files", T.boolean),
        P.pair("duplicate_pin_numbers_are_jumpers", T.boolean),
        P.pair("exclude_from_sim", T.boolean),
        P.mapped_collection(
          "properties",
          "property",
          (p) => p.name,
          T.item(Property, this)
        ),
        P.collection("pins", "pin", T.item(PinDefinition, this)),
        P.collection("children", "symbol", T.item(_LibSymbol, this)),
        P.collection("drawings", "arc", T.item(Arc2, this)),
        P.collection("drawings", "bezier", T.item(Bezier, this)),
        P.collection("drawings", "circle", T.item(Circle, this)),
        P.collection("drawings", "polyline", T.item(Polyline, this)),
        P.collection("drawings", "rectangle", T.item(Rectangle, this)),
        P.collection("drawings", "text", T.item(LibText, this)),
        P.collection("drawings", "textbox", T.item(TextBox, this)),
        P.pair("embedded_fonts", T.boolean),
        P.collection("embedded_files", "file", T.item(EmbeddedFile))
      )
    );
    for (const pin of this.pins) {
      this.#pins_by_number.set(pin.number.text, pin);
    }
    for (const property of this.properties.values()) {
      this.#properties_by_id.set(property.id, property);
    }
    for (const child of this.children) {
      const unit_num = child.unit;
      if (unit_num !== null) {
        const list = this.units.get(unit_num) ?? [];
        list.push(child);
        this.units.set(unit_num, list);
      }
    }
  }
  static {
    __name(this, "LibSymbol");
  }
  #pins_by_number;
  #properties_by_id;
  get root() {
    if (this.parent instanceof _LibSymbol) {
      return this.parent.root;
    }
    return this;
  }
  has_pin(number) {
    return this.#pins_by_number.has(number);
  }
  pin_by_number(number, style = 1) {
    if (this.has_pin(number)) {
      return this.#pins_by_number.get(number);
    }
    for (const child of this.children) {
      if ((child.style == 0 || child.style == style) && child.has_pin(number)) {
        return child.pin_by_number(number);
      }
    }
    throw new Error(
      `No pin numbered ${number} on library symbol ${this.name}`
    );
  }
  has_property_with_id(id) {
    return this.#properties_by_id.has(id);
  }
  property_by_id(id) {
    if (this.#properties_by_id.has(id)) {
      return this.#properties_by_id.get(id);
    }
    for (const child of this.children) {
      if (child.has_property_with_id(id)) {
        return child.property_by_id(id);
      }
    }
    return null;
  }
  get library_name() {
    if (this.name.includes(":")) {
      return this.name.split(":").at(0);
    }
    return "";
  }
  get library_item_name() {
    if (this.name.includes(":")) {
      return this.name.split(":").at(1);
    }
    return "";
  }
  get unit_count() {
    let count = this.units.size;
    if (this.units.has(0)) {
      count -= 1;
    }
    return count;
  }
  get unit() {
    const parts = this.name.split("_");
    if (parts.length < 3) {
      return 0;
    }
    return parseInt(parts.at(-2), 10);
  }
  get style() {
    const parts = this.name.split("_");
    if (parts.length < 3) {
      return 0;
    }
    return parseInt(parts.at(-1), 10);
  }
  get description() {
    return this.properties.get("ki_description")?.text ?? "";
  }
  get keywords() {
    return this.properties.get("ki_keywords")?.text ?? "";
  }
  get footprint_filters() {
    return this.properties.get("ki_fp_filters")?.text ?? "";
  }
  get units_interchangable() {
    return this.properties.get("ki_locked")?.text ? false : true;
  }
  resolve_text_var(name) {
    return this.parent?.resolve_text_var(name);
  }
};
var Property = class {
  constructor(expr, parent) {
    this.parent = parent;
    this.hide = false;
    this.show_name = false;
    this.do_not_autoplace = false;
    const parsed = parse_expr(
      expr,
      P.start("property"),
      P.positional("name", T.string),
      P.positional("text", T.string),
      P.pair("id", T.number),
      P.pair("hide", T.boolean),
      P.item("at", At),
      P.item("effects", Effects),
      P.atom("show_name"),
      P.atom("do_not_autoplace")
    );
    this.#effects = parsed["effects"];
    delete parsed["effects"];
    this.hide = this.hide || (this.#effects?.hide ?? false);
    Object.assign(this, parsed);
  }
  static {
    __name(this, "Property");
  }
  #effects;
  get effects() {
    if (this.#effects) {
      return this.#effects;
    } else if (this.parent instanceof SchematicSymbol) {
      this.#effects = new Effects();
    } else {
      warn(`Couldn't determine Effects for Property ${this.name}`);
    }
    return this.#effects;
  }
  set effects(e) {
    this.#effects = e;
  }
  get shown_text() {
    return expand_text_vars(this.text, this.parent);
  }
};
var PinDefinition = class {
  constructor(expr, parent) {
    this.parent = parent;
    this.hide = false;
    this.name = {
      text: "",
      effects: new Effects()
    };
    this.number = {
      text: "",
      effects: new Effects()
    };
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("pin"),
        P.positional("type", T.string),
        P.positional("shape", T.string),
        P.atom("hide"),
        P.item("at", At),
        P.pair("length", T.number),
        P.object(
          "name",
          this.name,
          P.positional("text", T.string),
          P.item("effects", Effects)
        ),
        P.object(
          "number",
          this.number,
          P.positional("text", T.string),
          P.item("effects", Effects)
        ),
        P.collection("alternates", "alternate", T.item(PinAlternate))
      )
    );
  }
  static {
    __name(this, "PinDefinition");
  }
  get unit() {
    return this.parent.unit;
  }
};
var PinAlternate = class {
  static {
    __name(this, "PinAlternate");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("alternate"),
        P.positional("name"),
        P.positional("type", T.string),
        P.positional("shaped", T.string)
      )
    );
  }
};
var RuleArea = class {
  constructor(expr, parent) {
    this.parent = parent;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("rule_area"),
        P.item("polyline", Polyline)
      )
    );
  }
  static {
    __name(this, "RuleArea");
  }
};
var SchematicSymbol = class {
  constructor(expr, parent) {
    this.parent = parent;
    this.in_bom = false;
    this.on_board = false;
    this.exclude_from_sim = false;
    this.dnp = false;
    this.fields_autoplaced = false;
    this.properties = /* @__PURE__ */ new Map();
    this.pins = [];
    this.instances = /* @__PURE__ */ new Map();
    const parsed = parse_expr(
      expr,
      P.start("symbol"),
      P.pair("lib_name", T.string),
      P.pair("lib_id", T.string),
      P.item("at", At),
      P.pair("mirror", T.string),
      P.pair("unit", T.number),
      P.pair("convert", T.number),
      P.pair("in_bom", T.boolean),
      P.pair("on_board", T.boolean),
      P.pair("exclude_from_sim", T.boolean),
      P.pair("dnp", T.boolean),
      P.atom("fields_autoplaced"),
      P.pair("uuid", T.string),
      P.mapped_collection(
        "properties",
        "property",
        (p) => p.name,
        T.item(Property, this)
      ),
      P.collection("pins", "pin", T.item(PinInstance, this)),
      P.object(
        "default_instance",
        this.default_instance,
        P.pair("reference", T.string),
        P.pair("unit", T.string),
        P.pair("value", T.string),
        P.pair("footprint", T.string)
      ),
      // (instances
      //    (project "kit-dev-coldfire-xilinx_5213"
      //      (path "/f5d7a48d-4587-4550-a504-c505ca11d375" (reference "R111") (unit 1))))
      P.object(
        "instances",
        {},
        P.collection(
          "projects",
          "project",
          T.object(
            null,
            P.start("project"),
            P.positional("name", T.string),
            P.collection(
              "paths",
              "path",
              T.object(
                null,
                P.start("path"),
                P.positional("path"),
                P.pair("reference", T.string),
                P.pair("value", T.string),
                P.pair("unit", T.number),
                P.pair("footprint", T.string)
              )
            )
          )
        )
      )
    );
    const parsed_instances = parsed["instances"];
    delete parsed["instances"];
    Object.assign(this, parsed);
    for (const project of parsed_instances?.["projects"] ?? []) {
      for (const path of project?.["paths"] ?? []) {
        const inst = new SchematicSymbolInstance();
        inst.path = path["path"];
        inst.reference = path["reference"];
        inst.value = path["value"];
        inst.unit = path["unit"];
        inst.footprint = path["footprint"];
        this.instances.set(inst.path, inst);
      }
    }
    if (this.get_property_text("Value") == void 0) {
      this.set_property_text("Value", this.default_instance.value);
    }
    if (!this.get_property_text("Footprint") == void 0) {
      this.set_property_text(
        "Footprint",
        this.default_instance.footprint
      );
    }
  }
  static {
    __name(this, "SchematicSymbol");
  }
  get lib_symbol() {
    return this.parent.lib_symbols.by_name(this.lib_name ?? this.lib_id);
  }
  get_property_text(name) {
    return this.properties.get(name)?.text;
  }
  set_property_text(name, val) {
    const prop = this.properties.get(name);
    if (prop) {
      prop.text = val;
    }
  }
  get reference() {
    return this.get_property_text("Reference") ?? "?";
  }
  set reference(val) {
    this.set_property_text("Reference", val);
  }
  get value() {
    return this.get_property_text("Value") ?? "";
  }
  set value(val) {
    this.set_property_text("Value", val);
  }
  get footprint() {
    return this.get_property_text("Footprint") ?? "";
  }
  set footprint(val) {
    this.set_property_text("Footprint", val);
  }
  get unit_suffix() {
    if (!this.unit || this.lib_symbol.unit_count <= 1) {
      return "";
    }
    const A = "A".charCodeAt(0);
    let unit = this.unit;
    let suffix = "";
    do {
      const x = (unit - 1) % 26;
      suffix = String.fromCharCode(A + x) + suffix;
      unit = Math.trunc((unit - x) / 26);
    } while (unit > 0);
    return suffix;
  }
  get unit_pins() {
    return this.pins.filter((pin) => {
      if (this.unit && pin.unit && this.unit != pin.unit) {
        return false;
      }
      return true;
    });
  }
  resolve_text_var(name) {
    if (this.properties.has(name)) {
      return this.properties.get(name)?.shown_text;
    }
    switch (name) {
      case "REFERENCE":
        return this.reference;
      case "VALUE":
        return this.value;
      case "FOOTPRINT":
        return this.footprint;
      case "DATASHEET":
        return this.properties.get("Datasheet")?.name;
      case "FOOTPRINT_LIBRARY":
        return this.footprint.split(":").at(0);
      case "FOOTPRINT_NAME":
        return this.footprint.split(":").at(-1);
      case "UNIT":
        return this.unit_suffix;
      case "SYMBOL_LIBRARY":
        return this.lib_symbol.library_name;
      case "SYMBOL_NAME":
        return this.lib_symbol.library_item_name;
      case "SYMBOL_DESCRIPTION":
        return this.lib_symbol.description;
      case "SYMBOL_KEYWORDS":
        return this.lib_symbol.keywords;
      case "EXCLUDE_FROM_BOM":
        return this.in_bom ? "" : "Excluded from BOM";
      case "EXCLUDE_FROM_BOARD":
        return this.on_board ? "" : "Excluded from board";
      case "DNP":
        return this.dnp ? "DNP" : "";
    }
    return this.parent.resolve_text_var(name);
  }
};
var SchematicSymbolInstance = class {
  static {
    __name(this, "SchematicSymbolInstance");
  }
  constructor() {
  }
};
var PinInstance = class {
  constructor(expr, parent) {
    this.parent = parent;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("pin"),
        P.positional("number", T.string),
        P.pair("uuid", T.string),
        P.pair("alternate", T.string)
      )
    );
  }
  static {
    __name(this, "PinInstance");
  }
  get definition() {
    return this.parent.lib_symbol.pin_by_number(
      this.number,
      this.parent.convert
    );
  }
  get unit() {
    return this.definition.unit;
  }
};
var SheetInstances = class {
  constructor(expr) {
    this.sheet_instances = /* @__PURE__ */ new Map();
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("sheet_instances"),
        P.mapped_collection(
          "sheet_instances",
          "path",
          (obj) => obj.path,
          T.item(SheetInstance)
        )
      )
    );
  }
  static {
    __name(this, "SheetInstances");
  }
  get(key) {
    return this.sheet_instances.get(key);
  }
};
var SheetInstance = class {
  static {
    __name(this, "SheetInstance");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        // note: start is "path"
        P.start("path"),
        P.positional("path", T.string),
        P.pair("page", T.string)
      )
    );
  }
};
var SymbolInstances = class {
  constructor(expr) {
    this.symbol_instances = /* @__PURE__ */ new Map();
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("symbol_instances"),
        P.mapped_collection(
          "symbol_instances",
          "path",
          (obj) => obj.path,
          T.item(SymbolInstance)
        )
      )
    );
  }
  static {
    __name(this, "SymbolInstances");
  }
  get(key) {
    return this.symbol_instances.get(key);
  }
};
var SymbolInstance = class {
  static {
    __name(this, "SymbolInstance");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        // note: start is "path"
        P.start("path"),
        P.positional("path", T.string),
        P.pair("reference", T.string),
        P.pair("unit", T.number),
        P.pair("value", T.string),
        P.pair("footprint", T.string)
      )
    );
  }
};
var SchematicSheet = class {
  constructor(expr, parent) {
    this.parent = parent;
    this.properties = /* @__PURE__ */ new Map();
    this.pins = [];
    this.instances = /* @__PURE__ */ new Map();
    const parsed = parse_expr(
      expr,
      P.start("sheet"),
      P.item("at", At),
      P.vec2("size"),
      P.item("stroke", Stroke),
      P.item("fill", Fill),
      P.pair("fields_autoplaced", T.boolean),
      P.pair("uuid", T.string),
      P.mapped_collection(
        "properties",
        "property",
        (prop) => prop.name,
        T.item(Property, this)
      ),
      P.collection("pins", "pin", T.item(SchematicSheetPin, this)),
      // (instances
      //   (project "kit-dev-coldfire-xilinx_5213"
      //     (path "/f5d7a48d-4587-4550-a504-c505ca11d375" (page "3"))))
      P.object(
        "instances",
        {},
        P.collection(
          "projects",
          "project",
          T.object(
            null,
            P.start("project"),
            P.positional("name", T.string),
            P.collection(
              "paths",
              "path",
              T.object(
                null,
                P.start("path"),
                P.positional("path"),
                P.pair("page", T.string)
              )
            )
          )
        )
      )
    );
    const parsed_instances = parsed["instances"];
    delete parsed["instances"];
    Object.assign(this, parsed);
    for (const project of parsed_instances?.["projects"] ?? []) {
      for (const path of project?.["paths"] ?? []) {
        const inst = new SchematicSheetInstance();
        inst.path = path["path"];
        inst.page = path["page"];
        this.instances.set(inst.path, inst);
      }
    }
  }
  static {
    __name(this, "SchematicSheet");
  }
  get_property_text(name) {
    return this.properties.get(name)?.text;
  }
  get sheetname() {
    return this.get_property_text("Sheetname") ?? this.get_property_text("Sheet name");
  }
  get sheetfile() {
    return this.get_property_text("Sheetfile") ?? this.get_property_text("Sheet file");
  }
  resolve_text_var(name) {
    return this.parent?.resolve_text_var(name);
  }
};
var SchematicSheetPin = class {
  constructor(expr, parent) {
    this.parent = parent;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("pin"),
        P.positional("name", T.string),
        P.positional("shape", T.string),
        P.item("at", At),
        P.item("effects", Effects),
        P.pair("uuid", T.string)
      )
    );
  }
  static {
    __name(this, "SchematicSheetPin");
  }
};
var SchematicSheetInstance = class {
  static {
    __name(this, "SchematicSheetInstance");
  }
};

// src/kicad/board.ts
var KicadPCB = class {
  constructor(filename, expr) {
    this.filename = filename;
    this.general = {
      thickness: 1.6,
      legacy_teardrops: false
    };
    this.title_block = new TitleBlock();
    this.properties = /* @__PURE__ */ new Map();
    this.layers = [];
    this.nets = [];
    this.footprints = [];
    this.zones = [];
    this.segments = [];
    this.vias = [];
    this.drawings = [];
    this.groups = [];
    this.embedded_fonts = false;
    this.embedded_files = [];
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("kicad_pcb"),
        P.pair("version", T.number),
        P.pair("generator", T.string),
        P.pair("generator_version", T.string),
        P.object(
          "general",
          {},
          P.pair("thickness", T.number),
          P.pair("legacy_teardrops", T.boolean)
        ),
        P.item("paper", Paper),
        P.item("title_block", TitleBlock),
        P.list("layers", T.item(Layer)),
        P.item("setup", Setup),
        P.mapped_collection(
          "properties",
          "property",
          (p) => p.name,
          T.item(Property2, this)
        ),
        P.collection("nets", "net", T.item(Net)),
        P.collection(
          "footprints",
          "footprint",
          T.item(Footprint, this)
        ),
        P.collection("zones", "zone", T.item(Zone, this)),
        P.collection("segments", "segment", T.item(LineSegment, this)),
        P.collection("segments", "arc", T.item(ArcSegment, this)),
        P.collection("vias", "via", T.item(Via, this)),
        P.collection("drawings", "dimension", T.item(Dimension, this)),
        P.collection("drawings", "gr_line", T.item(GrLine, this)),
        P.collection("drawings", "gr_circle", T.item(GrCircle, this)),
        P.collection("drawings", "gr_arc", T.item(GrArc, this)),
        P.collection("drawings", "gr_poly", T.item(GrPoly, this)),
        P.collection("drawings", "gr_rect", T.item(GrRect, this)),
        P.collection("drawings", "gr_text", T.item(GrText, this)),
        P.collection("groups", "group", T.item(Group)),
        P.pair("embedded_fonts", T.boolean),
        P.list("embedded_files", T.item(EmbeddedFile))
      )
    );
    this.nets.sort((a, b) => a.number - b.number);
  }
  static {
    __name(this, "KicadPCB");
  }
  *items() {
    yield* this.drawings;
    yield* this.vias;
    yield* this.segments;
    yield* this.zones;
    yield* this.footprints;
  }
  resolve_text_var(name) {
    if (name == "FILENAME") {
      return this.filename;
    }
    if (this.properties.has(name)) {
      return this.properties.get(name).value;
    }
    return this.title_block.resolve_text_var(name);
  }
  get edge_cuts_bbox() {
    let bbox = new BBox(0, 0, 0, 0);
    for (const item of this.drawings) {
      if (item.layer != "Edge.Cuts" || !(item instanceof GraphicItem2)) {
        continue;
      }
      bbox = BBox.combine([bbox, item.bbox]);
    }
    return bbox;
  }
  find_footprint(uuid_or_ref) {
    for (const fp of this.footprints) {
      if (fp.unique_id == uuid_or_ref || fp.reference == uuid_or_ref) {
        return fp;
      }
    }
    return null;
  }
  get_netname_by_number(net_number) {
    return this.nets[net_number]?.name;
  }
};
var Property2 = class {
  static {
    __name(this, "Property");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("property"),
        P.positional("name", T.string),
        P.positional("value", T.string)
      )
    );
  }
};
var LineSegment = class {
  constructor(expr, parent) {
    this.parent = parent;
    this.locked = false;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("segment"),
        P.vec2("start"),
        P.vec2("end"),
        P.pair("width", T.number),
        P.pair("layer", T.string),
        P.pair("net", T.number),
        P.atom("locked"),
        P.pair("uuid", T.string),
        P.pair("tstamp", T.string)
      )
    );
  }
  static {
    __name(this, "LineSegment");
  }
  get unique_id() {
    return this.uuid ?? this.tstamp;
  }
  get netname() {
    return this.parent.get_netname_by_number(this.net);
  }
};
var ArcSegment = class {
  constructor(expr, parent) {
    this.parent = parent;
    this.locked = false;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("arc"),
        P.vec2("start"),
        P.vec2("mid"),
        P.vec2("end"),
        P.pair("width", T.number),
        P.pair("layer", T.string),
        P.pair("net", T.number),
        P.atom("locked"),
        P.pair("tstamp", T.string),
        P.pair("uuid", T.string)
      )
    );
  }
  static {
    __name(this, "ArcSegment");
  }
  get unique_id() {
    return this.uuid ?? this.tstamp;
  }
  get netname() {
    return this.parent.get_netname_by_number(this.net);
  }
};
var Via = class {
  constructor(expr, parent) {
    this.parent = parent;
    this.type = "through-hole";
    this.remove_unused_layers = false;
    this.keep_end_layers = false;
    this.locked = false;
    this.free = false;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("via"),
        P.atom("type", ["blind", "micro", "through-hole"]),
        P.item("at", At),
        P.pair("size", T.number),
        P.pair("drill", T.number),
        P.list("layers", T.string),
        P.pair("net", T.number),
        P.atom("locked"),
        P.atom("free"),
        P.atom("remove_unused_layers"),
        P.atom("keep_end_layers"),
        P.pair("tstamp", T.string),
        P.pair("uuid", T.string)
      )
    );
  }
  static {
    __name(this, "Via");
  }
  get unique_id() {
    return this.uuid ?? this.tstamp;
  }
  get netname() {
    return this.parent.get_netname_by_number(this.net);
  }
};
var Zone = class {
  constructor(expr, parent) {
    this.parent = parent;
    this.locked = false;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("zone"),
        P.atom("locked"),
        P.pair("net", T.number),
        P.pair("net_name", T.string),
        P.pair("net_name", T.string),
        P.pair("name", T.string),
        P.pair("layer", T.string),
        P.list("layers", T.string),
        P.object(
          "hatch",
          {},
          P.positional("style", T.string),
          P.positional("pitch", T.number)
        ),
        P.pair("priority", T.number),
        P.object(
          "connect_pads",
          {},
          P.positional("type", T.string),
          P.pair("clearance", T.number)
        ),
        P.pair("min_thickness", T.number),
        P.pair("filled_areas_thickness", T.boolean),
        P.item("keepout", ZoneKeepout),
        P.item("fill", ZoneFill),
        P.collection("polygons", "polygon", T.item(Poly)),
        P.collection(
          "filled_polygons",
          "filled_polygon",
          T.item(FilledPolygon)
        ),
        P.pair("tstamp", T.string),
        P.pair("uuid", T.string)
      )
    );
  }
  static {
    __name(this, "Zone");
  }
  get unique_id() {
    return this.uuid ?? this.tstamp;
  }
};
var ZoneKeepout = class {
  static {
    __name(this, "ZoneKeepout");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("keepout"),
        P.pair("tracks", T.string),
        P.pair("vias", T.string),
        P.pair("pads", T.string),
        P.pair("copperpour", T.string),
        P.pair("footprints", T.string)
      )
    );
  }
};
var ZoneFill = class {
  constructor(expr) {
    this.fill = false;
    this.mode = "solid";
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("fill"),
        P.positional("fill", T.boolean),
        P.pair("mode", T.string),
        P.pair("thermal_gap", T.number),
        P.pair("thermal_bridge_width", T.number),
        P.expr(
          "smoothing",
          T.object(
            {},
            P.positional("style", T.string),
            P.pair("radius", T.number)
          )
        ),
        P.pair("radius", T.number),
        P.pair("island_removal_mode", T.number),
        P.pair("island_area_min", T.number),
        P.pair("hatch_thickness", T.number),
        P.pair("hatch_gap", T.number),
        P.pair("hatch_orientation", T.number),
        P.pair("hatch_smoothing_level", T.number),
        P.pair("hatch_smoothing_value", T.number),
        P.pair("hatch_border_algorithm", T.string),
        P.pair("hatch_min_hole_area", T.number)
      )
    );
  }
  static {
    __name(this, "ZoneFill");
  }
};
var Layer = class {
  static {
    __name(this, "Layer");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.positional("ordinal", T.number),
        P.positional("canonical_name", T.string),
        P.positional("type", T.string),
        P.positional("user_name", T.string)
      )
    );
  }
};
var Setup = class {
  constructor(expr) {
    this.allow_soldermask_bridges_in_footprints = false;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("setup"),
        P.pair("pad_to_mask_clearance", T.number),
        P.pair("solder_mask_min_width", T.number),
        P.pair("pad_to_paste_clearance", T.number),
        P.pair("pad_to_paste_clearance_ratio", T.number),
        P.pair("allow_soldermask_bridges_in_footprints", T.boolean),
        P.vec2("aux_axis_origin"),
        P.vec2("grid_origin"),
        P.item("pcbplotparams", PCBPlotParams),
        P.item("stackup", Stackup)
      )
    );
  }
  static {
    __name(this, "Setup");
  }
};
var PCBPlotParams = class {
  constructor(expr) {
    this.disableapertmacros = false;
    this.usegerberextensions = false;
    this.usegerberattributes = false;
    this.usegerberadvancedattributes = false;
    this.creategerberjobfile = false;
    this.svguseinch = false;
    this.excludeedgelayer = false;
    this.plotframeref = false;
    this.viasonmask = false;
    this.useauxorigin = false;
    this.pdf_front_fp_property_popups = true;
    this.pdf_back_fp_property_popups = true;
    this.pdf_metadata = true;
    this.pdf_single_document = false;
    this.dxfpolygonmode = false;
    this.dxfimperialunits = false;
    this.dxfusepcbnewfont = false;
    this.psnegative = false;
    this.psa4output = false;
    this.plotreference = false;
    this.plotvalue = false;
    this.plotinvisibletext = false;
    this.sketchpadsonfab = false;
    this.subtractmaskfromsilk = false;
    this.plot_black_and_white = true;
    this.plotpadnumbers = false;
    this.hidednponfab = false;
    this.sketchdnponfab = true;
    this.crossoutdnponfab = true;
    this.mirror = false;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("pcbplotparams"),
        P.pair("layerselection", T.number),
        P.pair("disableapertmacros", T.boolean),
        P.pair("usegerberextensions", T.boolean),
        P.pair("usegerberattributes", T.boolean),
        P.pair("usegerberadvancedattributes", T.boolean),
        P.pair("creategerberjobfile", T.boolean),
        P.pair("gerberprecision", T.number),
        P.pair("svguseinch", T.boolean),
        P.pair("svgprecision", T.number),
        P.pair("excludeedgelayer", T.boolean),
        P.pair("plotframeref", T.boolean),
        P.pair("viasonmask", T.boolean),
        P.pair("mode", T.number),
        P.pair("useauxorigin", T.boolean),
        P.pair("hpglpennumber", T.number),
        P.pair("hpglpenspeed", T.number),
        P.pair("hpglpendiameter", T.number),
        P.pair("pdf_front_fp_property_popups", T.boolean),
        P.pair("pdf_back_fp_property_popups", T.boolean),
        P.pair("pdf_metadata", T.boolean),
        P.pair("pdf_single_document", T.boolean),
        P.pair("dxfpolygonmode", T.boolean),
        P.pair("dxfimperialunits", T.boolean),
        P.pair("dxfusepcbnewfont", T.boolean),
        P.pair("psnegative", T.boolean),
        P.pair("psa4output", T.boolean),
        P.pair("plotreference", T.boolean),
        P.pair("plotvalue", T.boolean),
        P.pair("plotinvisibletext", T.boolean),
        P.pair("sketchpadsonfab", T.boolean),
        P.pair("subtractmaskfromsilk", T.boolean),
        P.pair("plotpadnumbers", T.boolean),
        P.pair("plot_black_and_white", T.boolean),
        P.pair("hidednponfab", T.boolean),
        P.pair("sketchdnponfab", T.boolean),
        P.pair("crossoutdnponfab", T.boolean),
        P.pair("outputformat", T.number),
        P.pair("mirror", T.boolean),
        P.pair("drillshape", T.number),
        P.pair("scaleselection", T.number),
        P.pair("outputdirectory", T.string),
        P.pair("plot_on_all_layers_selection", T.number),
        P.pair("dashed_line_dash_ratio", T.number),
        P.pair("dashed_line_gap_ratio", T.number)
      )
    );
  }
  static {
    __name(this, "PCBPlotParams");
  }
};
var Stackup = class {
  constructor(expr) {
    this.dielectric_constraints = false;
    this.castellated_pads = false;
    this.edge_plating = false;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("stackup"),
        P.pair("copper_finish", T.string),
        P.pair("dielectric_constraints", T.boolean),
        P.pair("edge_connector", T.string),
        P.pair("castellated_pads", T.boolean),
        P.pair("edge_plating", T.boolean),
        P.collection("layers", "layer", T.item(StackupLayer))
      )
    );
  }
  static {
    __name(this, "Stackup");
  }
};
var StackupLayer = class {
  static {
    __name(this, "StackupLayer");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("layer"),
        P.positional("name", T.string),
        P.pair("type", T.string),
        P.pair("color", T.string),
        P.pair("thickness", T.number),
        P.pair("material", T.string),
        P.pair("epsilon_r", T.number),
        P.pair("loss_tangent", T.number)
      )
    );
  }
};
var Dimension = class {
  constructor(expr, parent) {
    this.parent = parent;
    this.locked = false;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("dimension"),
        P.atom("locked"),
        P.pair("type", T.string),
        P.pair("layer", T.string),
        P.pair("tstamp", T.string),
        P.pair("uuid", T.string),
        P.list("pts", T.vec2),
        P.pair("height", T.number),
        P.pair("orientation", T.number),
        P.pair("leader_length", T.number),
        P.item("gr_text", GrText, this),
        P.item("format", DimensionFormat),
        P.item("style", DimensionStyle)
      )
    );
  }
  static {
    __name(this, "Dimension");
  }
  resolve_text_var(name) {
    return this.parent.resolve_text_var(name);
  }
  get start() {
    return this.pts.at(0) ?? new Vec2(0, 0);
  }
  get end() {
    return this.pts.at(-1) ?? new Vec2(0, 0);
  }
  get unique_id() {
    return this.uuid ?? this.tstamp;
  }
};
var DimensionFormat = class {
  constructor(expr) {
    this.suppress_zeroes = false;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("format"),
        P.pair("prefix", T.string),
        P.pair("suffix", T.string),
        P.pair("units", T.number),
        P.pair("units_format", T.number),
        P.pair("precision", T.number),
        P.pair("override_value", T.string),
        P.atom("suppress_zeroes")
      )
    );
  }
  static {
    __name(this, "DimensionFormat");
  }
};
var DimensionStyle = class {
  static {
    __name(this, "DimensionStyle");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("style"),
        P.pair("thickness", T.number),
        P.pair("arrow_length", T.number),
        P.pair("text_position_mode", T.number),
        P.pair("extension_height", T.number),
        P.pair("text_frame", T.number),
        P.pair("extension_offset", T.number),
        P.atom("keep_text_aligned")
      )
    );
  }
};
var Footprint = class {
  constructor(expr, parent) {
    this.parent = parent;
    this.locked = false;
    this.placed = false;
    this.attr = {
      through_hole: false,
      smd: false,
      virtual: false,
      board_only: false,
      exclude_from_pos_files: false,
      exclude_from_bom: false,
      allow_solder_mask_bridges: false,
      allow_missing_courtyard: false
    };
    this.properties = {};
    this.drawings = [];
    this.pads = [];
    this.#pads_by_number = /* @__PURE__ */ new Map();
    this.zones = [];
    this.models = [];
    this.embedded_fonts = false;
    this.embedded_files = [];
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("footprint"),
        P.positional("library_link", T.string),
        P.pair("version", T.number),
        P.pair("generator", T.string),
        P.atom("locked"),
        P.atom("placed"),
        P.pair("layer", T.string),
        P.pair("tedit", T.string),
        P.pair("tstamp", T.string),
        P.pair("uuid", T.string),
        P.pair("sheetname", T.string),
        P.pair("sheetfile", T.string),
        P.item("at", At),
        P.pair("descr", T.string),
        P.pair("tags", T.string),
        P.pair("path", T.string),
        P.pair("autoplace_cost90", T.number),
        P.pair("autoplace_cost180", T.number),
        P.pair("solder_mask_margin", T.number),
        P.pair("solder_paste_margin", T.number),
        P.pair("solder_paste_ratio", T.number),
        P.pair("clearance", T.number),
        P.pair("zone_connect", T.number),
        P.pair("thermal_width", T.number),
        P.pair("thermal_gap", T.number),
        P.pair("net_tie_pad_groups", T.string),
        P.object(
          "attr",
          this.attr,
          P.atom("through_hole"),
          P.atom("smd"),
          P.atom("virtual"),
          P.atom("board_only"),
          P.atom("exclude_from_pos_files"),
          P.atom("exclude_from_bom"),
          P.atom("allow_solder_mask_bridges"),
          P.atom("allow_missing_courtyard")
        ),
        P.dict("properties", "property", T.item(SymbolProperty, this)),
        P.collection("drawings", "fp_line", T.item(FpLine, this)),
        P.collection("drawings", "fp_circle", T.item(FpCircle, this)),
        P.collection("drawings", "fp_arc", T.item(FpArc, this)),
        P.collection("drawings", "fp_poly", T.item(FpPoly, this)),
        P.collection("drawings", "fp_rect", T.item(FpRect, this)),
        P.collection("drawings", "fp_text", T.item(FpText, this)),
        P.collection("zones", "zone", T.item(Zone, this)),
        P.collection("models", "model", T.item(Model)),
        P.collection("pads", "pad", T.item(Pad, this)),
        P.pair("embedded_fonts", T.boolean),
        P.list("embedded_files", T.item(EmbeddedFile))
      )
    );
    for (const pad of this.pads) {
      this.#pads_by_number.set(pad.number, pad);
    }
    for (const d of this.drawings) {
      if (!(d instanceof FpText)) {
        continue;
      }
      if (d.type == "reference") {
        this.reference = d.text;
      }
      if (d.type == "value") {
        this.value = d.text;
      }
    }
    for (const [prop_name, prop] of Object.entries(this.properties)) {
      if (this.reference === void 0 && prop_name == "Reference") {
        this.reference = prop.value;
      }
      if (this.value === void 0 && prop_name == "Value") {
        this.value = prop.value;
      }
    }
  }
  static {
    __name(this, "Footprint");
  }
  #pads_by_number;
  #bbox;
  get unique_id() {
    return this.uuid ?? this.tstamp;
  }
  *items() {
    yield* this.drawings ?? [];
    yield* this.zones ?? [];
    yield* this.pads.values() ?? [];
    yield* Object.values(this.properties).filter(
      (prop) => prop.has_symbol_prop
    );
  }
  resolve_text_var(name) {
    switch (name) {
      case "REFERENCE":
        return this.reference;
      case "VALUE":
        return this.value;
      case "LAYER":
        return this.layer;
      case "FOOTPRINT_LIBRARY":
        return this.library_link.split(":").at(0);
      case "FOOTPRINT_NAME":
        return this.library_link.split(":").at(-1);
    }
    const pad_expr = /^(NET_NAME|NET_CLASS|PIN_NAME)\(.+?\)$/.exec(name);
    if (pad_expr?.length == 3) {
      const [_, expr_type, pad_name] = pad_expr;
      switch (expr_type) {
        case "NET_NAME":
          return this.pad_by_number(pad_name)?.net.number.toString();
        case "NET_CLASS":
          return this.pad_by_number(pad_name)?.net.name;
        case "PIN_NAME":
          return this.pad_by_number(pad_name)?.pinfunction;
      }
    }
    if (this.properties[name] !== void 0) {
      return this.properties[name].value;
    }
    return this.parent.resolve_text_var(name);
  }
  pad_by_number(number) {
    return this.#pads_by_number.get(number);
  }
  /**
   * Get the nominal bounding box for this footprint.
   *
   * This does not take into account text drawings.
   */
  get bbox() {
    if (!this.#bbox) {
      let bbox = new BBox(
        this.at.position.x - 0.25,
        this.at.position.y - 0.25,
        0.5,
        0.5
      );
      const matrix = Matrix3.translation(
        this.at.position.x,
        this.at.position.y
      ).rotate_self(Angle.deg_to_rad(this.at.rotation));
      for (const item of this.drawings) {
        if (item instanceof FpText || item instanceof SymbolProperty) {
          continue;
        }
        bbox = BBox.combine([bbox, item.bbox.transform(matrix)]);
      }
      bbox.context = this;
      this.#bbox = bbox;
    }
    return this.#bbox;
  }
};
var SymbolProperty = class {
  constructor(expr, parent) {
    this.parent = parent;
    // symbol properties
    // https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html#_symbol_properties
    this.id = 0;
    this.unlocked = false;
    this.hide = false;
    this.at = new At();
    this.effects = new Effects();
    this.layer = "F.SilkS";
    const is_newer = expr instanceof Array && expr.length > 3;
    if (is_newer) {
      this.has_symbol_prop = true;
      Object.assign(
        this,
        parse_expr(
          expr,
          P.positional("value", T.string),
          P.pair("id", T.number),
          P.item("at", At),
          P.pair("layer", T.string),
          P.pair("uuid", T.string),
          P.atom("unlocked"),
          P.atom("hide"),
          P.item("effects", Effects)
        )
      );
    } else {
      this.has_symbol_prop = false;
      Object.assign(
        this,
        parse_expr(expr, P.positional("value", T.string))
      );
    }
  }
  static {
    __name(this, "SymbolProperty");
  }
  get shown_text() {
    return expand_text_vars(this.value, this.parent);
  }
  get unique_id() {
    return this.uuid;
  }
};
var GraphicItem2 = class {
  constructor() {
    this.locked = false;
    this.stroke = Stroke.default_value();
  }
  static {
    __name(this, "GraphicItem");
  }
  get unique_id() {
    return this.uuid ?? this.tstamp;
  }
  get stroke_params() {
    let pcb = void 0;
    if (this.parent instanceof KicadPCB) {
      pcb = this.parent;
    } else if (this.parent instanceof Footprint) {
      pcb = this.parent.parent;
    }
    const plot_cfg = pcb?.setup?.pcbplotparams;
    return {
      stroke: this.stroke,
      dashed_line_gap_ratio: plot_cfg?.dashed_line_gap_ratio ?? 3,
      dashed_line_dash_ratio: plot_cfg?.dashed_line_dash_ratio ?? 12
    };
  }
  /**
   * Get the nominal bounding box for the item. This does not include any
   * stroke or other expansion.
   */
  get bbox() {
    return new BBox(0, 0, 0, 0);
  }
};
var Line = class extends GraphicItem2 {
  constructor(expr, parent) {
    super();
    this.parent = parent;
    const static_this = this.constructor;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start(static_this.expr_start),
        P.atom("locked"),
        P.pair("layer", T.string),
        P.vec2("start"),
        P.vec2("end"),
        P.pair("width", T.number),
        P.pair("uuid", T.string),
        P.pair("tstamp", T.string),
        P.item("stroke", Stroke)
      )
    );
    this.width ??= this.stroke?.width || 0;
  }
  static {
    __name(this, "Line");
  }
  static {
    this.expr_start = "unset";
  }
  get bbox() {
    return BBox.from_points([this.start, this.end]);
  }
};
var GrLine = class extends Line {
  static {
    __name(this, "GrLine");
  }
  static {
    this.expr_start = "gr_line";
  }
};
var FpLine = class extends Line {
  static {
    __name(this, "FpLine");
  }
  static {
    this.expr_start = "fp_line";
  }
};
var Circle2 = class extends GraphicItem2 {
  constructor(expr, parent) {
    super();
    this.parent = parent;
    const static_this = this.constructor;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start(static_this.expr_start),
        P.atom("locked"),
        P.vec2("center"),
        P.vec2("end"),
        P.pair("width", T.number),
        P.pair("fill", T.string),
        P.pair("layer", T.string),
        P.pair("uuid", T.string),
        P.pair("tstamp", T.string),
        P.item("stroke", Stroke)
      )
    );
    this.width ??= this.stroke?.width || 0;
  }
  static {
    __name(this, "Circle");
  }
  static {
    this.expr_start = "unset";
  }
  get bbox() {
    const radius = this.center.sub(this.end).magnitude;
    const radial = new Vec2(radius, radius);
    return BBox.from_points([
      this.center.sub(radial),
      this.center.add(radial)
    ]);
  }
};
var GrCircle = class extends Circle2 {
  static {
    __name(this, "GrCircle");
  }
  static {
    this.expr_start = "gr_circle";
  }
};
var FpCircle = class extends Circle2 {
  static {
    __name(this, "FpCircle");
  }
  static {
    this.expr_start = "fp_circle";
  }
};
var Arc3 = class extends GraphicItem2 {
  constructor(expr, parent) {
    super();
    this.parent = parent;
    const static_this = this.constructor;
    const parsed = parse_expr(
      expr,
      P.start(static_this.expr_start),
      P.atom("locked"),
      P.pair("layer", T.string),
      P.vec2("start"),
      P.vec2("mid"),
      P.vec2("end"),
      P.pair("angle", T.number),
      P.pair("width", T.number),
      P.pair("uuid", T.string),
      P.pair("tstamp", T.string),
      P.item("stroke", Stroke)
    );
    if (parsed["angle"] !== void 0) {
      const angle = Angle.from_degrees(parsed["angle"]).normalize720();
      const center = parsed["start"];
      let start = parsed["end"];
      let end = angle.negative().rotate_point(start, center);
      if (angle.degrees < 0) {
        [start, end] = [end, start];
      }
      this.#arc = Arc.from_center_start_end(
        center,
        start,
        end,
        parsed["width"]
      );
      parsed["start"] = this.#arc.start_point;
      parsed["mid"] = this.#arc.mid_point;
      parsed["end"] = this.#arc.end_point;
      delete parsed["angle"];
    } else {
      this.#arc = Arc.from_three_points(
        parsed["start"],
        parsed["mid"],
        parsed["end"],
        parsed["width"]
      );
    }
    Object.assign(this, parsed);
    this.width ??= this.stroke?.width ?? this.#arc.width;
    this.#arc.width = this.width;
  }
  static {
    __name(this, "Arc");
  }
  static {
    this.expr_start = "unset";
  }
  #arc;
  get arc() {
    return this.#arc;
  }
  get bbox() {
    return this.arc.bbox;
  }
};
var GrArc = class extends Arc3 {
  static {
    __name(this, "GrArc");
  }
  static {
    this.expr_start = "gr_arc";
  }
};
var FpArc = class extends Arc3 {
  static {
    __name(this, "FpArc");
  }
  static {
    this.expr_start = "fp_arc";
  }
};
var PolyArc = class {
  static {
    __name(this, "PolyArc");
  }
  #arc;
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("arc"),
        P.vec2("start"),
        P.vec2("mid"),
        P.vec2("end")
      )
    );
    this.#arc = Arc.from_three_points(this.start, this.mid, this.end);
  }
  get arc() {
    return this.#arc;
  }
  get bbox() {
    return this.#arc.bbox;
  }
};
var Poly = class extends GraphicItem2 {
  constructor(expr, parent) {
    super();
    this.parent = parent;
    const static_this = this.constructor;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start(static_this.expr_start),
        P.atom("locked"),
        P.pair("layer", T.string),
        P.atom("island"),
        P.list(
          "pts",
          T.choice(["xy", T.vec2], ["arc", T.item(PolyArc)])
        ),
        P.pair("width", T.number),
        P.pair("fill", T.string),
        P.pair("uuid", T.string),
        P.pair("tstamp", T.string),
        P.item("stroke", Stroke)
      )
    );
    this.width ??= this.stroke?.width || 0;
    this.#polyline_pts = this.pts.flatMap((pt) => {
      if (pt instanceof Vec2) {
        return [pt];
      } else {
        return pt.arc.to_polyline();
      }
    });
  }
  static {
    __name(this, "Poly");
  }
  static {
    this.expr_start = "polygon";
  }
  #polyline_pts;
  get polyline() {
    return this.#polyline_pts;
  }
  get bbox() {
    return BBox.from_points(this.#polyline_pts);
  }
};
var FilledPolygon = class extends Poly {
  static {
    __name(this, "FilledPolygon");
  }
  static {
    this.expr_start = "filled_polygon";
  }
};
var GrPoly = class extends Poly {
  static {
    __name(this, "GrPoly");
  }
  static {
    this.expr_start = "gr_poly";
  }
};
var FpPoly = class extends Poly {
  static {
    __name(this, "FpPoly");
  }
  static {
    this.expr_start = "fp_poly";
  }
};
var Rect = class extends GraphicItem2 {
  constructor(expr, parent) {
    super();
    this.parent = parent;
    const static_this = this.constructor;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start(static_this.expr_start),
        P.atom("locked"),
        P.vec2("start"),
        P.vec2("end"),
        P.pair("layer", T.string),
        P.pair("width", T.number),
        P.pair("fill", T.string),
        P.pair("uuid", T.string),
        P.pair("tstamp", T.string),
        P.item("stroke", Stroke)
      )
    );
    this.width ??= this.stroke?.width || 0;
  }
  static {
    __name(this, "Rect");
  }
  static {
    this.expr_start = "rect";
  }
  get bbox() {
    return BBox.from_points([this.start, this.end]);
  }
};
var GrRect = class extends Rect {
  static {
    __name(this, "GrRect");
  }
  static {
    this.expr_start = "gr_rect";
  }
};
var FpRect = class extends Rect {
  static {
    __name(this, "FpRect");
  }
  static {
    this.expr_start = "fp_rect";
  }
};
var TextRenderCache = class {
  static {
    __name(this, "TextRenderCache");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("render_cache"),
        P.positional("text", T.string),
        P.positional("angle", T.number),
        P.collection("polygons", "polygon", T.item(Poly))
      )
    );
    for (const poly of this.polygons) {
      poly.fill = "solid";
    }
  }
};
var Text2 = class {
  constructor() {
    this.unlocked = false;
    this.hide = false;
    this.effects = new Effects();
  }
  static {
    __name(this, "Text");
  }
  static {
    this.common_expr_defs = [
      P.item("at", At),
      P.atom("hide"),
      P.atom("unlocked"),
      P.object(
        "layer",
        {},
        P.positional("name", T.string),
        P.atom("knockout")
      ),
      P.pair("tstamp", T.string),
      P.pair("uuid", T.string),
      P.item("effects", Effects),
      P.item("render_cache", TextRenderCache)
    ];
  }
  get shown_text() {
    return expand_text_vars(this.text, this.parent);
  }
  get unique_id() {
    return this.uuid ?? this.tstamp;
  }
};
var FpText = class extends Text2 {
  constructor(expr, parent) {
    super();
    this.parent = parent;
    this.locked = false;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("fp_text"),
        P.atom("locked"),
        P.positional("type", T.string),
        P.positional("text", T.string),
        ...Text2.common_expr_defs
      )
    );
  }
  static {
    __name(this, "FpText");
  }
};
var GrText = class extends Text2 {
  constructor(expr, parent) {
    super();
    this.parent = parent;
    this.locked = false;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("gr_text"),
        P.atom("locked"),
        P.positional("text", T.string),
        ...Text2.common_expr_defs
      )
    );
  }
  static {
    __name(this, "GrText");
  }
};
var Pad = class {
  constructor(expr, parent) {
    this.parent = parent;
    // I hate this
    this.type = "thru_hole";
    this.locked = false;
    this.remove_unused_layers = false;
    const parsed = parse_expr(
      expr,
      P.start("pad"),
      P.positional("number", T.string),
      P.positional("type", T.string),
      P.positional("shape", T.string),
      P.item("at", At),
      P.atom("locked"),
      P.vec2("size"),
      P.vec2("rect_delta"),
      P.list("layers", T.string),
      P.pair("roundrect_rratio", T.number),
      P.pair("chamfer_ratio", T.number),
      P.expr(
        "chamfer",
        T.object(
          {},
          P.atom("top_right"),
          P.atom("top_left"),
          P.atom("bottom_right"),
          P.atom("bottom_left")
        )
      ),
      P.pair("pinfunction", T.string),
      P.pair("pintype", T.string),
      P.pair("solder_mask_margin", T.number),
      P.pair("solder_paste_margin", T.number),
      P.pair("solder_paste_margin_ratio", T.number),
      P.pair("clearance", T.number),
      P.pair("thermal_width", T.number),
      P.pair("thermal_gap", T.number),
      P.pair("thermal_bridge_angle", T.number),
      P.pair("zone_connect", T.number),
      P.pair("tstamp", T.string),
      P.pair("uuid", T.string),
      P.pair("remove_unused_layers", T.boolean),
      P.item("drill", PadDrill),
      P.item("net", Net),
      P.item("options", PadOptions),
      P.expr("primitives", (obj, name, expr2) => {
        const parsed2 = parse_expr(
          expr2,
          P.start("primitives"),
          P.collection("items", "gr_line", T.item(GrLine, this)),
          P.collection("items", "gr_circle", T.item(GrCircle, this)),
          P.collection("items", "gr_arc", T.item(GrArc, this)),
          P.collection("items", "gr_rect", T.item(GrRect, this)),
          P.collection("items", "gr_poly", T.item(GrPoly, this))
        );
        return parsed2?.["items"];
      })
    );
    Object.assign(this, parsed);
  }
  static {
    __name(this, "Pad");
  }
  get unique_id() {
    return this.uuid ?? this.tstamp;
  }
  get netname() {
    return this.net?.name;
  }
};
var PadDrill = class {
  constructor(expr) {
    this.oval = false;
    this.diameter = 0;
    this.width = 0;
    this.offset = new Vec2(0, 0);
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("drill"),
        P.atom("oval"),
        P.positional("diameter", T.number),
        P.positional("width", T.number),
        P.vec2("offset")
      )
    );
  }
  static {
    __name(this, "PadDrill");
  }
};
var PadOptions = class {
  static {
    __name(this, "PadOptions");
  }
  constructor(expr) {
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("options"),
        P.pair("clearance", T.string),
        P.pair("anchor", T.string)
      )
    );
  }
};
var Model = class {
  constructor(expr) {
    this.hide = false;
    this.opacity = 1;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("model"),
        P.positional("filename", T.string),
        P.atom("hide"),
        P.pair("opacity", T.number),
        P.object("offset", {}, P.list("xyz", T.number)),
        P.object("scale", {}, P.list("xyz", T.number)),
        P.object("rotate", {}, P.list("xyz", T.number))
      )
    );
  }
  static {
    __name(this, "Model");
  }
};
var Group = class {
  constructor(expr) {
    this.locked = false;
    Object.assign(
      this,
      parse_expr(
        expr,
        P.start("group"),
        P.positional("name", T.string),
        P.atom("locked"),
        P.pair("id", T.string),
        P.pair("uuid", T.string),
        P.list("members", T.string)
      )
    );
  }
  static {
    __name(this, "Group");
  }
  get unique_id() {
    return this.uuid ?? this.id;
  }
};
export {
  KicadPCB,
  KicadSch
};
