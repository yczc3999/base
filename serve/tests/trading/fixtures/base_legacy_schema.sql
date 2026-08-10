--
-- PostgreSQL database dump
--


-- Dumped from database version 18.4 (Ubuntu 18.4-1.pgdg24.04+1)
-- Dumped by pg_dump version 18.4 (Ubuntu 18.4-1.pgdg24.04+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: admin_login_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_login_logs (
    id integer NOT NULL,
    user_id integer NOT NULL,
    username character varying(50) NOT NULL,
    ip character varying(50),
    user_agent character varying(500),
    status smallint DEFAULT 1 NOT NULL,
    remark character varying(255),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: admin_login_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.admin_login_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: admin_login_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.admin_login_logs_id_seq OWNED BY public.admin_login_logs.id;


--
-- Name: admin_operation_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_operation_logs (
    id integer NOT NULL,
    user_id integer NOT NULL,
    username character varying(50) NOT NULL,
    module character varying(100) NOT NULL,
    action character varying(100) NOT NULL,
    method character varying(10) NOT NULL,
    url character varying(500) NOT NULL,
    params text,
    ip character varying(50),
    user_agent character varying(500),
    status_code integer DEFAULT 0 NOT NULL,
    duration integer DEFAULT 0 NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: admin_operation_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.admin_operation_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: admin_operation_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.admin_operation_logs_id_seq OWNED BY public.admin_operation_logs.id;


--
-- Name: admin_user_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_user_roles (
    admin_user_id integer NOT NULL,
    role_id integer NOT NULL
);


--
-- Name: admin_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.admin_users (
    id integer NOT NULL,
    username character varying(50) NOT NULL,
    password character varying(255) NOT NULL,
    nickname character varying(50),
    avatar character varying(255),
    email character varying(100),
    phone character varying(20),
    status smallint DEFAULT 1 NOT NULL,
    is_super_admin boolean DEFAULT false NOT NULL,
    token_version integer DEFAULT 0 NOT NULL,
    last_login_at timestamp without time zone,
    last_login_ip character varying(50),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    password_changed_at timestamp without time zone
);


--
-- Name: admin_users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.admin_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: admin_users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.admin_users_id_seq OWNED BY public.admin_users.id;


--
-- Name: article_keywords; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.article_keywords (
    article_id bigint NOT NULL,
    keyword_id bigint NOT NULL,
    is_primary boolean DEFAULT false NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: articles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.articles (
    id integer NOT NULL,
    title character varying(200) NOT NULL,
    slug character varying(200),
    summary character varying(500),
    excerpt text,
    content text NOT NULL,
    cover_image character varying(500),
    author_id integer,
    view_count integer DEFAULT 0 NOT NULL,
    is_pinned boolean DEFAULT false NOT NULL,
    sort integer DEFAULT 0 NOT NULL,
    source smallint DEFAULT 0 NOT NULL,
    source_url character varying(500),
    raw_content text,
    ai_processed boolean DEFAULT false NOT NULL,
    status smallint DEFAULT 0 NOT NULL,
    published_at timestamp without time zone,
    simhash bigint,
    slug_history jsonb DEFAULT '[]'::jsonb NOT NULL,
    deleted_at timestamp without time zone,
    scheduled_at timestamp without time zone,
    retry_count smallint DEFAULT 0 NOT NULL,
    last_publish_error text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: articles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.articles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: articles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.articles_id_seq OWNED BY public.articles.id;


--
-- Name: db_backups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.db_backups (
    id integer NOT NULL,
    filename character varying(255) NOT NULL,
    file_size bigint DEFAULT 0 NOT NULL,
    status character varying(16) DEFAULT 'ok'::character varying NOT NULL,
    started_at timestamp without time zone,
    finished_at timestamp without time zone,
    error_msg character varying(500),
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: db_backups_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.db_backups_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: db_backups_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.db_backups_id_seq OWNED BY public.db_backups.id;


--
-- Name: dict_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dict_items (
    id integer NOT NULL,
    dict_id integer NOT NULL,
    value character varying(100) NOT NULL,
    label character varying(100) NOT NULL,
    sort integer DEFAULT 0 NOT NULL,
    status smallint DEFAULT 1 NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: dict_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dict_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dict_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dict_items_id_seq OWNED BY public.dict_items.id;


--
-- Name: dicts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dicts (
    id integer NOT NULL,
    type_name character varying(50) NOT NULL,
    description character varying(200),
    status smallint DEFAULT 1 NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: dicts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dicts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dicts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dicts_id_seq OWNED BY public.dicts.id;


--
-- Name: files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.files (
    id integer NOT NULL,
    name character varying(200) NOT NULL,
    original_name character varying(200) NOT NULL,
    path character varying(500) NOT NULL,
    url character varying(500) NOT NULL,
    platform character varying(50) NOT NULL,
    mime_type character varying(100),
    size integer DEFAULT 0 NOT NULL,
    ext character varying(20),
    is_private boolean DEFAULT false NOT NULL,
    user_id integer,
    category character varying(50) DEFAULT 'default'::character varying,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: files_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.files_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: files_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.files_id_seq OWNED BY public.files.id;


--
-- Name: keywords; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.keywords (
    id bigint NOT NULL,
    keyword character varying(200) NOT NULL,
    keyword_norm character varying(200) NOT NULL,
    slug character varying(200),
    stage character varying(16) DEFAULT 'candidate'::character varying NOT NULL,
    review_status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    source_code character varying(32) NOT NULL,
    seed_keyword character varying(200),
    expanded_as_seed_at timestamp without time zone,
    fetched_at timestamp without time zone DEFAULT now() NOT NULL,
    metrics_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    ai_review_json jsonb,
    color character varying(20),
    description character varying(200),
    sort integer DEFAULT 0 NOT NULL,
    article_count integer DEFAULT 0 NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_keywords_stage_review CHECK ((((stage)::text = 'candidate'::text) OR (((stage)::text = 'approved'::text) AND ((review_status)::text = ANY (ARRAY[('human_approved'::character varying)::text, ('ai_approved'::character varying)::text]))) OR (((stage)::text = 'archived'::text) AND ((review_status)::text = ANY (ARRAY[('human_rejected'::character varying)::text, ('ai_rejected'::character varying)::text])))))
);


--
-- Name: keywords_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.keywords_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: keywords_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.keywords_id_seq OWNED BY public.keywords.id;


--
-- Name: menus; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.menus (
    id integer NOT NULL,
    parent_id integer DEFAULT 0 NOT NULL,
    type smallint DEFAULT 0 NOT NULL,
    slug character varying(50) NOT NULL,
    label character varying(50) NOT NULL,
    icon character varying(100),
    path character varying(200),
    template_path character varying(200),
    redirect character varying(200),
    perms character varying(100),
    link character varying(500),
    link_target character varying(10) DEFAULT '_self'::character varying,
    is_cache boolean DEFAULT true NOT NULL,
    is_affix boolean DEFAULT false NOT NULL,
    is_visible boolean DEFAULT true NOT NULL,
    badge character varying(20),
    sort integer DEFAULT 0 NOT NULL,
    status smallint DEFAULT 1 NOT NULL,
    remark character varying(255),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: menus_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.menus_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: menus_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.menus_id_seq OWNED BY public.menus.id;


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    id integer NOT NULL,
    user_id integer NOT NULL,
    title character varying(200) NOT NULL,
    content text,
    type smallint DEFAULT 0 NOT NULL,
    is_read boolean DEFAULT false NOT NULL,
    sender_id integer,
    sender_name character varying(50),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE messages; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.messages IS '系统消息表';


--
-- Name: COLUMN messages.user_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.messages.user_id IS '接收者用户ID(admin)';


--
-- Name: COLUMN messages.type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.messages.type IS '消息类型: 0=系统通知 1=审批消息 2=告警消息';


--
-- Name: COLUMN messages.is_read; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.messages.is_read IS '是否已读';


--
-- Name: messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.messages_id_seq OWNED BY public.messages.id;


--
-- Name: publish_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.publish_log (
    id bigint NOT NULL,
    action character varying(32) NOT NULL,
    level character varying(8) DEFAULT 'info'::character varying NOT NULL,
    article_id integer,
    msg text,
    payload jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: publish_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.publish_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: publish_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.publish_log_id_seq OWNED BY public.publish_log.id;


--
-- Name: role_menus; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.role_menus (
    role_id integer NOT NULL,
    menu_id integer NOT NULL
);


--
-- Name: roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.roles (
    id integer NOT NULL,
    name character varying(50) NOT NULL,
    label character varying(50) NOT NULL,
    remark character varying(255),
    sort integer DEFAULT 0 NOT NULL,
    status smallint DEFAULT 1 NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: roles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.roles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: roles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.roles_id_seq OWNED BY public.roles.id;


--
-- Name: settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.settings (
    id integer NOT NULL,
    category character varying(50) NOT NULL,
    name character varying(100) NOT NULL,
    label character varying(100),
    value text DEFAULT ''::text NOT NULL,
    remark character varying(255),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: settings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: settings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.settings_id_seq OWNED BY public.settings.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username character varying(50) NOT NULL,
    password character varying(255) NOT NULL,
    nickname character varying(50),
    avatar character varying(255),
    email character varying(100),
    phone character varying(20),
    status smallint DEFAULT 1 NOT NULL,
    token_version integer DEFAULT 0 NOT NULL,
    last_login_at timestamp without time zone,
    last_login_ip character varying(50),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: admin_login_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_login_logs ALTER COLUMN id SET DEFAULT nextval('public.admin_login_logs_id_seq'::regclass);


--
-- Name: admin_operation_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_operation_logs ALTER COLUMN id SET DEFAULT nextval('public.admin_operation_logs_id_seq'::regclass);


--
-- Name: admin_users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_users ALTER COLUMN id SET DEFAULT nextval('public.admin_users_id_seq'::regclass);


--
-- Name: articles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.articles ALTER COLUMN id SET DEFAULT nextval('public.articles_id_seq'::regclass);


--
-- Name: db_backups id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.db_backups ALTER COLUMN id SET DEFAULT nextval('public.db_backups_id_seq'::regclass);


--
-- Name: dict_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dict_items ALTER COLUMN id SET DEFAULT nextval('public.dict_items_id_seq'::regclass);


--
-- Name: dicts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dicts ALTER COLUMN id SET DEFAULT nextval('public.dicts_id_seq'::regclass);


--
-- Name: files id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files ALTER COLUMN id SET DEFAULT nextval('public.files_id_seq'::regclass);


--
-- Name: keywords id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.keywords ALTER COLUMN id SET DEFAULT nextval('public.keywords_id_seq'::regclass);


--
-- Name: menus id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.menus ALTER COLUMN id SET DEFAULT nextval('public.menus_id_seq'::regclass);


--
-- Name: messages id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages ALTER COLUMN id SET DEFAULT nextval('public.messages_id_seq'::regclass);


--
-- Name: publish_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publish_log ALTER COLUMN id SET DEFAULT nextval('public.publish_log_id_seq'::regclass);


--
-- Name: roles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles ALTER COLUMN id SET DEFAULT nextval('public.roles_id_seq'::regclass);


--
-- Name: settings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settings ALTER COLUMN id SET DEFAULT nextval('public.settings_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: admin_login_logs admin_login_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_login_logs
    ADD CONSTRAINT admin_login_logs_pkey PRIMARY KEY (id);


--
-- Name: admin_operation_logs admin_operation_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_operation_logs
    ADD CONSTRAINT admin_operation_logs_pkey PRIMARY KEY (id);


--
-- Name: admin_user_roles admin_user_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_user_roles
    ADD CONSTRAINT admin_user_roles_pkey PRIMARY KEY (admin_user_id, role_id);


--
-- Name: admin_users admin_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_users
    ADD CONSTRAINT admin_users_pkey PRIMARY KEY (id);


--
-- Name: admin_users admin_users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_users
    ADD CONSTRAINT admin_users_username_key UNIQUE (username);


--
-- Name: article_keywords article_keywords_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.article_keywords
    ADD CONSTRAINT article_keywords_pkey PRIMARY KEY (article_id, keyword_id);


--
-- Name: articles articles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.articles
    ADD CONSTRAINT articles_pkey PRIMARY KEY (id);


--
-- Name: articles articles_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.articles
    ADD CONSTRAINT articles_slug_key UNIQUE (slug);


--
-- Name: db_backups db_backups_filename_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.db_backups
    ADD CONSTRAINT db_backups_filename_key UNIQUE (filename);


--
-- Name: db_backups db_backups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.db_backups
    ADD CONSTRAINT db_backups_pkey PRIMARY KEY (id);


--
-- Name: dict_items dict_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dict_items
    ADD CONSTRAINT dict_items_pkey PRIMARY KEY (id);


--
-- Name: dicts dicts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dicts
    ADD CONSTRAINT dicts_pkey PRIMARY KEY (id);


--
-- Name: dicts dicts_type_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dicts
    ADD CONSTRAINT dicts_type_name_key UNIQUE (type_name);


--
-- Name: files files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT files_pkey PRIMARY KEY (id);


--
-- Name: keywords keywords_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.keywords
    ADD CONSTRAINT keywords_pkey PRIMARY KEY (id);


--
-- Name: menus menus_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.menus
    ADD CONSTRAINT menus_pkey PRIMARY KEY (id);


--
-- Name: menus menus_slug_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.menus
    ADD CONSTRAINT menus_slug_unique UNIQUE (slug);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: publish_log publish_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.publish_log
    ADD CONSTRAINT publish_log_pkey PRIMARY KEY (id);


--
-- Name: role_menus role_menus_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_menus
    ADD CONSTRAINT role_menus_pkey PRIMARY KEY (role_id, menu_id);


--
-- Name: roles roles_name_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_name_unique UNIQUE (name);


--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- Name: settings settings_category_name_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settings
    ADD CONSTRAINT settings_category_name_unique UNIQUE (category, name);


--
-- Name: settings settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (id);


--
-- Name: dict_items uq_dict_items_dict_value; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dict_items
    ADD CONSTRAINT uq_dict_items_dict_value UNIQUE (dict_id, value);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: admin_users_email_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX admin_users_email_unique ON public.admin_users USING btree (email) WHERE ((email IS NOT NULL) AND ((email)::text <> ''::text));


--
-- Name: idx_admin_login_logs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_login_logs_created_at ON public.admin_login_logs USING btree (created_at);


--
-- Name: idx_admin_login_logs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_login_logs_user_id ON public.admin_login_logs USING btree (user_id);


--
-- Name: idx_admin_operation_logs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_operation_logs_created_at ON public.admin_operation_logs USING btree (created_at);


--
-- Name: idx_admin_operation_logs_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_admin_operation_logs_user_id ON public.admin_operation_logs USING btree (user_id);


--
-- Name: idx_article_keywords_keyword_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_article_keywords_keyword_time ON public.article_keywords USING btree (keyword_id, created_at DESC);


--
-- Name: idx_article_keywords_primary; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_article_keywords_primary ON public.article_keywords USING btree (keyword_id) WHERE (is_primary = true);


--
-- Name: idx_articles_drafts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_articles_drafts ON public.articles USING btree (created_at DESC) WHERE ((status = 0) AND (deleted_at IS NULL));


--
-- Name: idx_articles_due_to_publish; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_articles_due_to_publish ON public.articles USING btree (scheduled_at) WHERE ((status = 0) AND (scheduled_at IS NOT NULL) AND (deleted_at IS NULL));


--
-- Name: idx_articles_live; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_articles_live ON public.articles USING btree (published_at DESC) WHERE ((status = 1) AND (deleted_at IS NULL));


--
-- Name: idx_articles_simhash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_articles_simhash ON public.articles USING btree (simhash) WHERE (simhash IS NOT NULL);


--
-- Name: idx_articles_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_articles_slug ON public.articles USING btree (slug);


--
-- Name: idx_articles_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_articles_status ON public.articles USING btree (status);


--
-- Name: idx_dict_items_dict_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dict_items_dict_id ON public.dict_items USING btree (dict_id);


--
-- Name: idx_files_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_files_category ON public.files USING btree (category);


--
-- Name: idx_files_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_files_created_at ON public.files USING btree (created_at);


--
-- Name: idx_files_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_files_user_id ON public.files USING btree (user_id);


--
-- Name: idx_keywords_review; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_keywords_review ON public.keywords USING btree (review_status) WHERE ((stage)::text = 'candidate'::text);


--
-- Name: idx_keywords_seed_unexpanded; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_keywords_seed_unexpanded ON public.keywords USING btree (expanded_as_seed_at) WHERE (expanded_as_seed_at IS NULL);


--
-- Name: idx_keywords_source_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_keywords_source_time ON public.keywords USING btree (source_code, fetched_at DESC);


--
-- Name: idx_keywords_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_keywords_stage ON public.keywords USING btree (stage);


--
-- Name: idx_log_action_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_log_action_time ON public.publish_log USING btree (action, created_at DESC);


--
-- Name: idx_log_article; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_log_article ON public.publish_log USING btree (article_id, created_at DESC) WHERE (article_id IS NOT NULL);


--
-- Name: idx_log_error_recent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_log_error_recent ON public.publish_log USING btree (created_at DESC) WHERE ((level)::text = 'error'::text);


--
-- Name: idx_log_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_log_time ON public.publish_log USING btree (created_at DESC);


--
-- Name: idx_menus_parent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_menus_parent_id ON public.menus USING btree (parent_id);


--
-- Name: idx_menus_sort; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_menus_sort ON public.menus USING btree (sort);


--
-- Name: idx_messages_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_created_at ON public.messages USING btree (created_at);


--
-- Name: idx_messages_is_read; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_is_read ON public.messages USING btree (user_id, is_read);


--
-- Name: idx_messages_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_user_id ON public.messages USING btree (user_id);


--
-- Name: uq_keywords_norm; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_keywords_norm ON public.keywords USING btree (keyword_norm);


--
-- Name: uq_keywords_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_keywords_slug ON public.keywords USING btree (slug) WHERE (slug IS NOT NULL);


--
-- Name: admin_user_roles admin_user_roles_admin_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_user_roles
    ADD CONSTRAINT admin_user_roles_admin_user_id_fkey FOREIGN KEY (admin_user_id) REFERENCES public.admin_users(id) ON DELETE CASCADE;


--
-- Name: admin_user_roles admin_user_roles_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.admin_user_roles
    ADD CONSTRAINT admin_user_roles_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.roles(id) ON DELETE CASCADE;


--
-- Name: article_keywords article_keywords_article_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.article_keywords
    ADD CONSTRAINT article_keywords_article_id_fkey FOREIGN KEY (article_id) REFERENCES public.articles(id) ON DELETE CASCADE;


--
-- Name: article_keywords article_keywords_keyword_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.article_keywords
    ADD CONSTRAINT article_keywords_keyword_id_fkey FOREIGN KEY (keyword_id) REFERENCES public.keywords(id) ON DELETE CASCADE;


--
-- Name: dict_items dict_items_dict_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dict_items
    ADD CONSTRAINT dict_items_dict_id_fkey FOREIGN KEY (dict_id) REFERENCES public.dicts(id) ON DELETE CASCADE;


--
-- Name: role_menus role_menus_menu_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_menus
    ADD CONSTRAINT role_menus_menu_id_fkey FOREIGN KEY (menu_id) REFERENCES public.menus(id) ON DELETE CASCADE;


--
-- Name: role_menus role_menus_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_menus
    ADD CONSTRAINT role_menus_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.roles(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--
