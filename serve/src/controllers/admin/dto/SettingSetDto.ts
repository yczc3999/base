import { IsString, IsNotEmpty, IsOptional } from 'class-validator';

/**
 * 写入配置参数
 */
export class SettingSetDto {
  /** 配置类别 */
  @IsString()
  @IsNotEmpty({ message: 'category 不能为空' })
  category: string;

  /** 配置键名 */
  @IsString()
  @IsNotEmpty({ message: 'key 不能为空' })
  key: string;

  /** 配置值（支持任意类型） */
  @IsOptional()
  value: any;
}
