import NProgress from 'nprogress'
import 'nprogress/nprogress.css'

NProgress.configure({
  showSpinner: false,
  minimum: 0.1,
  speed: 300,
  trickleSpeed: 150,
})

// P2-4: 尊重 prefers-reduced-motion —— NProgress 用内联 transition 动画，全局
// reduced-motion 样式无法覆盖内联样式，需在 JS 侧把 speed 归零（直接跳变）。
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
function applyMotionPref() {
  NProgress.configure({ speed: reducedMotion.matches ? 0 : 300 })
}
applyMotionPref()
reducedMotion.addEventListener('change', applyMotionPref)

export default NProgress
