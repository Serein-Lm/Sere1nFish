import { useState, useRef, useEffect, useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { message } from 'antd'
import { login } from '../../services/authService'
import { resolveLoginReturnPath } from '../../utils/authNavigation'
import './Login.css'

interface LoginForm {
  username: string
  password: string
  key: string
}

interface InputActiveState {
  username: boolean
  password: boolean
  key: boolean
}

// 粒子类
class Particle {
  x: number
  y: number
  baseX: number
  baseY: number
  size: number
  vx: number
  vy: number
  opacity: number
  type: string
  rotation: number
  rotationSpeed: number

  constructor(x: number, y: number) {
    this.x = x
    this.y = y
    this.baseX = x
    this.baseY = y
    const types = ['triangle', 'square', 'circle', 'diamond', 'cross']
    this.type = types[Math.floor(Math.random() * types.length)]
    this.size = (Math.random() * 5 + 4) * 1.5
    this.vx = (Math.random() - 0.5) * 0.5
    this.vy = (Math.random() - 0.5) * 0.5
    this.opacity = Math.random() * 0.4 + 0.2
    this.rotation = Math.random() * Math.PI * 2
    this.rotationSpeed = (Math.random() - 0.5) * 0.02
  }

  update(ctx: CanvasRenderingContext2D, mouseX: number, mouseY: number, mouseRadius: number) {
    this.x += this.vx
    this.y += this.vy
    this.rotation += this.rotationSpeed

    const width = ctx.canvas.width
    const height = ctx.canvas.height

    if (this.x < 0 || this.x > width) this.vx *= -0.8
    if (this.y < 0 || this.y > height) this.vy *= -0.8

    const dx = mouseX - this.x
    const dy = mouseY - this.y
    const distance = Math.sqrt(dx * dx + dy * dy)

    if (distance < mouseRadius && distance > 0) {
      const force = (mouseRadius - distance) / mouseRadius
      this.vx += (dx / distance) * force * 0.6
      this.vy += (dy / distance) * force * 0.6
    }

    this.vx *= 0.95
    this.vy *= 0.95

    if (Math.abs(this.x - this.baseX) > 200 || Math.abs(this.y - this.baseY) > 200) {
      this.vx += (this.baseX - this.x) * 0.002
      this.vy += (this.baseY - this.y) * 0.002
    }
  }

  draw(ctx: CanvasRenderingContext2D) {
    ctx.save()
    ctx.translate(this.x, this.y)
    ctx.rotate(this.rotation)
    ctx.globalAlpha = this.opacity
    ctx.strokeStyle = `rgba(255, 255, 255, ${this.opacity * 0.8})`
    ctx.lineWidth = 1

    const size = this.size * 1.5
    ctx.beginPath()
    switch (this.type) {
      case 'triangle':
        ctx.moveTo(0, -size)
        ctx.lineTo(-size, size)
        ctx.lineTo(size, size)
        ctx.closePath()
        break
      case 'square':
        ctx.strokeRect(-size, -size, size * 2, size * 2)
        break
      case 'circle':
        ctx.arc(0, 0, this.size, 0, Math.PI * 2)
        break
      case 'diamond':
        ctx.moveTo(0, -size)
        ctx.lineTo(size, 0)
        ctx.lineTo(0, size)
        ctx.lineTo(-size, 0)
        ctx.closePath()
        break
      case 'cross':
        ctx.moveTo(-size, 0)
        ctx.lineTo(size, 0)
        ctx.moveTo(0, -size)
        ctx.lineTo(0, size)
        break
    }
    ctx.stroke()
    ctx.restore()
  }
}

export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const [loginForm, setLoginForm] = useState<LoginForm>({ username: '', password: '', key: '' })
  const [loading, setLoading] = useState(false)
  const [isFormActive, setIsFormActive] = useState(false)
  const [isInputActive, setIsInputActive] = useState<InputActiveState>({ username: false, password: false, key: false })
  const [errors, setErrors] = useState<Partial<LoginForm>>({})
  const [messageApi, contextHolder] = message.useMessage()

  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const particlesRef = useRef<Particle[]>([])
  const mouseRef = useRef({ x: 0, y: 0, radius: 180 })
  const animationRef = useRef<number>(0)

  // 初始化粒子系统
  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const updateCanvasSize = () => {
      canvas.width = container.clientWidth
      canvas.height = container.clientHeight
    }
    updateCanvasSize()
    window.addEventListener('resize', updateCanvasSize)

    // 创建粒子
    particlesRef.current = []
    for (let i = 0; i < 180; i++) {
      particlesRef.current.push(new Particle(Math.random() * canvas.width, Math.random() * canvas.height))
    }

    const animate = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      for (let i = 0; i < particlesRef.current.length; i += 2) {
        const p = particlesRef.current[i]
        p.update(ctx, mouseRef.current.x, mouseRef.current.y, mouseRef.current.radius)
        p.draw(ctx)
      }
      animationRef.current = requestAnimationFrame(animate)
    }
    animate()

    return () => {
      window.removeEventListener('resize', updateCanvasSize)
      if (animationRef.current) cancelAnimationFrame(animationRef.current)
    }
  }, [])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!containerRef.current) return
    const rect = containerRef.current.getBoundingClientRect()
    mouseRef.current.x = e.clientX - rect.left
    mouseRef.current.y = e.clientY - rect.top
  }, [])

  const handleInputChange = (field: keyof LoginForm, value: string) => {
    setLoginForm(prev => ({ ...prev, [field]: value }))
    if (errors[field]) setErrors(prev => ({ ...prev, [field]: undefined }))
  }

  const handleInputFocus = (field: keyof InputActiveState) => {
    setIsInputActive(prev => ({ ...prev, [field]: true }))
    setIsFormActive(true)
  }

  const handleInputBlur = (field: keyof InputActiveState) => {
    setIsInputActive(prev => ({ ...prev, [field]: false }))
    if (!loginForm.username && !loginForm.password && !loginForm.key) {
      setIsFormActive(false)
    }
  }

  const validate = (): boolean => {
    const newErrors: Partial<LoginForm> = {}
    if (!loginForm.username) newErrors.username = '请输入用户名'
    if (!loginForm.password) newErrors.password = '请输入密码'
    if (!loginForm.key) newErrors.key = '请输入访问密钥'
    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }

  const handleLogin = async () => {
    if (!validate()) return
    setLoading(true)
    messageApi.destroy()

    try {
      const result = await login({
        username: loginForm.username,
        password: loginForm.password,
        key: loginForm.key,
      })

      localStorage.setItem('token', result.access_token)
      localStorage.setItem('userInfo', JSON.stringify({ username: loginForm.username }))
      navigate(resolveLoginReturnPath(location.state, location.search), { replace: true })
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : ''
      messageApi.error(errMsg || '登录失败，请检查用户名、密码或访问密钥')
      setLoginForm(prev => ({ ...prev, password: '', key: '' }))
    } finally {
      setLoading(false)
    }
  }

  const handleBoxEnter = () => { mouseRef.current.radius = 250 }
  const handleBoxLeave = () => { mouseRef.current.radius = 180 }

  return (
    <div className="login-container" ref={containerRef} onMouseMove={handleMouseMove}>
      {contextHolder}
      {/* 动态背景层 */}
      <div className="dynamic-background">
        <div className="grid-layer"></div>
        <div className="gradient-sphere"></div>
        <div className="light-beams">
          <div className="beam beam-1"></div>
          <div className="beam beam-2"></div>
          <div className="beam beam-3"></div>
        </div>
        <div className="floating-shapes">
          <div className="shape shape-1"></div>
          <div className="shape shape-2"></div>
          <div className="shape shape-3"></div>
        </div>
        <div className="tech-circles">
          <div className="circle circle-1"></div>
          <div className="circle circle-2"></div>
          <div className="circle circle-3"></div>
        </div>
        <div className="ripple-background">
          <div className="ripple ripple-1"></div>
          <div className="ripple ripple-2"></div>
          <div className="ripple ripple-3"></div>
          <div className="ripple ripple-4"></div>
        </div>
      </div>

      {/* 粒子系统 */}
      <div className="particles-container">
        <canvas ref={canvasRef} className="particles-canvas"></canvas>
      </div>

      <div className={`login-content ${isFormActive ? 'content-active' : ''}`}
        onMouseEnter={handleBoxEnter} onMouseLeave={handleBoxLeave}>
        <div className={`login-box ${isFormActive ? 'form-active' : ''}`}>
          <div className="box-aura"></div>
          <div className="box-glow"></div>

          {/* Logo和标题 */}
          <div className="brand">
            <div className="logo-container">
              <div className="logo-rings">
                <div className="ring ring-1"></div>
                <div className="ring ring-2"></div>
                <div className="ring ring-3"></div>
              </div>
              <div className="logo-icon-wrapper">
                <svg className="logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect>
                  <line x1="8" y1="21" x2="16" y2="21"></line>
                  <line x1="12" y1="17" x2="12" y2="21"></line>
                </svg>
                <div className="icon-glow"></div>
              </div>
            </div>
            <div className="title-container">
              <h1 className="title">Sere1nFish</h1>
              <div className="title-decoration">
                <span className="line"></span>
                <span className="dot"></span>
                <span className="line"></span>
              </div>
              <p className="subtitle">AI钓鱼中台</p>
            </div>
          </div>

          {/* 登录表单 */}
          <form className="login-form" onSubmit={e => { e.preventDefault(); handleLogin() }}>
            <div className="form-container">
              <div className={`input-group ${isInputActive.username ? 'input-active' : ''}`}>
                <div className="input-label">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                    <circle cx="12" cy="7" r="4"></circle>
                  </svg>
                  <span>身份验证</span>
                </div>
                <div className="input-wrapper">
                  <input type="text" placeholder="请输入用户名" value={loginForm.username}
                    onChange={e => handleInputChange('username', e.target.value)}
                    onFocus={() => handleInputFocus('username')}
                    onBlur={() => handleInputBlur('username')} />
                </div>
                {errors.username && <span className="error-msg">{errors.username}</span>}
                <div className="input-line"></div>
              </div>

              <div className={`input-group ${isInputActive.password ? 'input-active' : ''}`}>
                <div className="input-label">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                    <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                    <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                  </svg>
                  <span>安全密钥</span>
                </div>
                <div className="input-wrapper">
                  <input type="password" placeholder="请输入密码" value={loginForm.password}
                    onChange={e => handleInputChange('password', e.target.value)}
                    onFocus={() => handleInputFocus('password')}
                    onBlur={() => handleInputBlur('password')}
                    onKeyDown={e => e.key === 'Enter' && handleLogin()} />
                </div>
                {errors.password && <span className="error-msg">{errors.password}</span>}
                <div className="input-line"></div>
              </div>

              <div className={`input-group ${isInputActive.key ? 'input-active' : ''}`}>
                <div className="input-label">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                    <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"></path>
                  </svg>
                  <span>访问令牌</span>
                </div>
                <div className="input-wrapper">
                  <input type="text" placeholder="请输入访问密钥" value={loginForm.key}
                    onChange={e => handleInputChange('key', e.target.value)}
                    onFocus={() => handleInputFocus('key')}
                    onBlur={() => handleInputBlur('key')}
                    onKeyDown={e => e.key === 'Enter' && handleLogin()} />
                </div>
                {errors.key && <span className="error-msg">{errors.key}</span>}
                <div className="input-line"></div>
              </div>

              <button type="submit" className={`login-button ${loading ? 'is-loading' : ''}`} disabled={loading}>
                <div className="button-content">
                  <span className="button-text">{loading ? '安全验证中...' : '安全登录'}</span>
                  <div className="button-background"></div>
                  <div className="button-border"></div>
                  <div className="button-glow"></div>
                  <div className="button-particles">
                    <div className="particle particle-1"></div>
                    <div className="particle particle-2"></div>
                    <div className="particle particle-3"></div>
                    <div className="particle particle-4"></div>
                  </div>
                  <div className="button-shine"></div>
                </div>
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
