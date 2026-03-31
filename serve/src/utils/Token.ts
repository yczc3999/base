import jwt from 'jsonwebtoken';

/**
 * JWT Token 载荷接口
 *
 * 定义 token 中携带的用户信息结构。
 */
export interface TokenPayload {
  /** 用户 ID */
  userId: number;
  /** token 版本号，用于实现 token 失效机制（修改密码/登出时递增） */
  tokenVersion: number;
  /** 用户类型：'admin' = 管理员，'client' = 前端用户 */
  scope: 'admin' | 'client';
  /** 允许扩展自定义字段 */
  [key: string]: any;
}

/**
 * JWT 工具类
 *
 * 封装 jsonwebtoken 库，提供 token 签发和验证方法。
 * 密钥和过期时间从环境变量读取：JWT_SECRET / JWT_EXPIRES_IN。
 *
 * 所有方法为静态方法，无需实例化即可使用。
 */
export class TokenUtil {
  /**
   * 签发 JWT token
   *
   * 使用 JWT_SECRET 环境变量作为签名密钥，
   * JWT_EXPIRES_IN 环境变量设置过期时间（默认 7 天）。
   *
   * @param payload - token 载荷，包含 userId 和 tokenVersion
   * @returns 签名后的 JWT token 字符串
   */
  static sign(payload: TokenPayload): string {
    const secret = process.env.JWT_SECRET || 'default-secret';
    const expiresIn = process.env.JWT_EXPIRES_IN || '7d';
    return jwt.sign(payload, secret, { expiresIn: expiresIn as jwt.SignOptions['expiresIn'] });
  }

  /**
   * 验证并解析 JWT token
   *
   * 验证 token 签名有效性和是否过期，验证通过后返回解析出的载荷。
   * 验证失败时抛出 jsonwebtoken 的异常（如 JsonWebTokenError / TokenExpiredError）。
   *
   * @param token - 待验证的 JWT token 字符串
   * @returns 解析后的 token 载荷对象
   * @throws JsonWebTokenError token 签名无效时抛出
   * @throws TokenExpiredError token 已过期时抛出
   */
  static verify(token: string): TokenPayload {
    const secret = process.env.JWT_SECRET || 'default-secret';
    return jwt.verify(token, secret) as TokenPayload;
  }
}
