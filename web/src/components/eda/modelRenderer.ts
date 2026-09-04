/**
 * The three.js half of the 3D preview — deliberately isolated so it is a
 * chunk of its own.
 *
 * three plus its loaders is ~600 KB, and the CAD tab is the only place
 * that ever needs it (and only once a 3D model is actually selected). So
 * this module is imported *dynamically* by `ModelPreview.tsx` and nothing
 * else references it statically; Rollup therefore code-splits it out of
 * the main bundle. `model3dChunk.test.ts` is what keeps that true — it
 * fails if `three` is ever imported anywhere but here, or if `ModelPreview`
 * stops loading this lazily. Same rigour as the KiCanvas chunk guard.
 *
 * `mountModelViewer` fetches and parses the model, builds a neutral
 * KiCad-like scene (perspective camera, ambient + directional light, a
 * ground grid), frames it to its bounding box, and returns a handle whose
 * `dispose()` releases the WebGL context and all GPU resources. It throws
 * on any fetch/parse failure so the caller can fall back to the
 * "unavailable" message — jsdom has no WebGL, so tests mock this module
 * rather than run it.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { VRMLLoader } from "three/examples/jsm/loaders/VRMLLoader.js";

export type ModelFormat = "glb" | "wrl";

export interface ModelViewerHandle {
  /** Stop the render loop and release the WebGL context + GPU buffers. */
  dispose(): void;
}

export interface MountOptions {
  src: string;
  format: ModelFormat;
  /** Abort the initial fetch if the caller unmounts mid-load. */
  signal?: AbortSignal;
}

/** Fetch + parse the model into a three.js object, or throw. */
async function loadModel(opts: MountOptions): Promise<THREE.Object3D> {
  // Same credentials stance as `lib/api.ts`: the preview routes are
  // workspace-scoped and ride the session cookie. Same-origin, so this is
  // belt-and-braces, but explicit is better than relying on the default.
  const res = await fetch(opts.src, {
    credentials: "include",
    signal: opts.signal,
  });
  if (!res.ok) {
    throw new Error(`model fetch failed: ${res.status}`);
  }

  if (opts.format === "wrl") {
    const text = await res.text();
    // VRMLLoader.parse is synchronous and throws on malformed input.
    return new VRMLLoader().parse(text, "");
  }

  const buffer = await res.arrayBuffer();
  const gltf = await new Promise<{ scene: THREE.Object3D }>((resolve, reject) => {
    new GLTFLoader().parse(buffer, "", resolve, reject);
  });
  return gltf.scene;
}

/** Recentre `model` on the origin and return the framing metrics. */
function frame(model: THREE.Object3D): { center: THREE.Vector3; radius: number } {
  const box = new THREE.Box3().setFromObject(model);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  // Guard a degenerate/empty bbox (an empty scene) so the camera maths
  // below never divides by zero.
  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  return { center, radius: maxDim / 2 };
}

/** Depth-first dispose of every geometry/material a scene owns. */
function disposeObject(root: THREE.Object3D): void {
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    mesh.geometry?.dispose?.();
    const material = mesh.material;
    if (Array.isArray(material)) {
      material.forEach((m) => m.dispose());
    } else {
      material?.dispose?.();
    }
  });
}

export async function mountModelViewer(
  host: HTMLElement,
  opts: MountOptions,
): Promise<ModelViewerHandle> {
  const model = await loadModel(opts);
  const { center, radius } = frame(model);

  const width = host.clientWidth || 1;
  const height = host.clientHeight || 1;

  const scene = new THREE.Scene();

  const camera = new THREE.PerspectiveCamera(
    45,
    width / height,
    radius / 100,
    radius * 100,
  );
  // Look at the part from a three-quarter angle, KiCad's default-ish view,
  // pulled back far enough to fit the bounding sphere in the 45° frustum.
  const distance = radius * 3;
  camera.position.set(
    center.x + distance,
    center.y + distance,
    center.z + distance,
  );

  // Neutral rig: an ambient term so nothing is pure black, a key light
  // roughly over the camera's shoulder, and a hemisphere fill for the
  // undersides. Colours are deliberately plain — this reads geometry, not
  // materials.
  scene.add(new THREE.AmbientLight(0xffffff, 0.6));
  const key = new THREE.DirectionalLight(0xffffff, 0.8);
  key.position.set(1, 1, 1);
  scene.add(key);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 0.4));

  // A ground grid sized to the part gives orientation and scale, the way
  // KiCad's 3D viewer does.
  const grid = new THREE.GridHelper(radius * 8, 16, 0x888888, 0xcccccc);
  grid.position.y = center.y - radius;
  scene.add(grid);

  scene.add(model);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setSize(width, height);
  host.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.copy(center);
  controls.enableDamping = true;
  controls.update();

  let frameId = 0;
  const tick = () => {
    controls.update();
    renderer.render(scene, camera);
    frameId = requestAnimationFrame(tick);
  };
  frameId = requestAnimationFrame(tick);

  const onResize = () => {
    const w = host.clientWidth || 1;
    const h = host.clientHeight || 1;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  };
  const observer = new ResizeObserver(onResize);
  observer.observe(host);

  return {
    dispose() {
      cancelAnimationFrame(frameId);
      observer.disconnect();
      controls.dispose();
      disposeObject(model);
      grid.geometry.dispose();
      (grid.material as THREE.Material).dispose();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
