import { IsString, IsNotEmpty, MinLength } from 'class-validator';

/**
 * 修改密码参数
 */
export class ChangePasswordDto {
  /** 原密码 */
  @IsString({ message: '原密码必须是字符串' })
  @IsNotEmpty({ message: '原密码不能为空' })
  oldPassword: string;

  /** 新密码 */
  @IsString({ message: '新密码必须是字符串' })
  @IsNotEmpty({ message: '新密码不能为空' })
  @MinLength(6, { message: '新密码长度不能少于6位' })
  newPassword: string;
}
