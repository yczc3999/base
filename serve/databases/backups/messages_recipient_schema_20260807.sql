--
-- PostgreSQL database dump
--

\restrict vcAQgXJhTT6IUs1xohGEGbFdXDOHU1dehUt1HezR9LwZytnmVaZw5czKjOaDXJJ

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
-- Name: messages; Type: TABLE; Schema: public; Owner: base_user
--

CREATE TABLE public.messages (
    id integer NOT NULL,
    recipient_id bigint CONSTRAINT messages_user_id_not_null NOT NULL,
    title character varying(200) NOT NULL,
    content text,
    type smallint DEFAULT 0 NOT NULL,
    is_read boolean DEFAULT false NOT NULL,
    sender_id integer,
    sender_name character varying(50),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    recipient_type character varying(20) DEFAULT 'admin'::character varying NOT NULL,
    event_id bigint,
    event_code character varying(80),
    biz_type character varying(50),
    biz_id bigint,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    action_path character varying(500),
    idempotency_key character varying(64),
    read_at timestamp with time zone,
    archived_at timestamp with time zone,
    CONSTRAINT ck_messages_read_state CHECK ((((is_read = false) AND (read_at IS NULL)) OR ((is_read = true) AND (read_at IS NOT NULL)))),
    CONSTRAINT ck_messages_recipient_id CHECK ((recipient_id > 0)),
    CONSTRAINT ck_messages_recipient_type CHECK (((recipient_type)::text = ANY ((ARRAY['admin'::character varying, 'customer'::character varying, 'parttime'::character varying])::text[])))
);


ALTER TABLE public.messages OWNER TO base_user;

--
-- Name: COLUMN messages.recipient_id; Type: COMMENT; Schema: public; Owner: base_user
--

COMMENT ON COLUMN public.messages.recipient_id IS '类型化收件人 ID；由 recipient_type 解释';


--
-- Name: COLUMN messages.is_read; Type: COMMENT; Schema: public; Owner: base_user
--

COMMENT ON COLUMN public.messages.is_read IS '旧接口兼容布尔值；必须与 read_at 是否为空一致';


--
-- Name: COLUMN messages.recipient_type; Type: COMMENT; Schema: public; Owner: base_user
--

COMMENT ON COLUMN public.messages.recipient_type IS '收件人类型：admin/customer/parttime';


--
-- Name: COLUMN messages.event_id; Type: COMMENT; Schema: public; Owner: base_user
--

COMMENT ON COLUMN public.messages.event_id IS '产生本消息的 notification_event；legacy 消息可空';


--
-- Name: COLUMN messages.event_code; Type: COMMENT; Schema: public; Owner: base_user
--

COMMENT ON COLUMN public.messages.event_code IS '稳定业务事件码的消息快照';


--
-- Name: COLUMN messages.biz_type; Type: COMMENT; Schema: public; Owner: base_user
--

COMMENT ON COLUMN public.messages.biz_type IS '业务引用类型；与 biz_id 组成类型化引用';


--
-- Name: COLUMN messages.biz_id; Type: COMMENT; Schema: public; Owner: base_user
--

COMMENT ON COLUMN public.messages.biz_id IS '业务引用 ID；不建多态外键';


--
-- Name: COLUMN messages.payload; Type: COMMENT; Schema: public; Owner: base_user
--

COMMENT ON COLUMN public.messages.payload IS '站内消息渲染所需的非敏感结构化快照';


--
-- Name: COLUMN messages.action_path; Type: COMMENT; Schema: public; Owner: base_user
--

COMMENT ON COLUMN public.messages.action_path IS '用户点击消息后的站内相对路径';


--
-- Name: COLUMN messages.idempotency_key; Type: COMMENT; Schema: public; Owner: base_user
--

COMMENT ON COLUMN public.messages.idempotency_key IS '站内消息幂等键；legacy 消息可空';


--
-- Name: COLUMN messages.read_at; Type: COMMENT; Schema: public; Owner: base_user
--

COMMENT ON COLUMN public.messages.read_at IS '实际已读时间；legacy 已读消息使用原 updated_at 兼容回填';


--
-- Name: COLUMN messages.archived_at; Type: COMMENT; Schema: public; Owner: base_user
--

COMMENT ON COLUMN public.messages.archived_at IS '用户归档时间；归档不删除消息事实';


--
-- Name: messages_id_seq; Type: SEQUENCE; Schema: public; Owner: base_user
--

CREATE SEQUENCE public.messages_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.messages_id_seq OWNER TO base_user;

--
-- Name: messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: base_user
--

ALTER SEQUENCE public.messages_id_seq OWNED BY public.messages.id;


--
-- Name: messages id; Type: DEFAULT; Schema: public; Owner: base_user
--

ALTER TABLE ONLY public.messages ALTER COLUMN id SET DEFAULT nextval('public.messages_id_seq'::regclass);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: base_user
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: messages uq_messages_idempotency_key; Type: CONSTRAINT; Schema: public; Owner: base_user
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT uq_messages_idempotency_key UNIQUE (idempotency_key);


--
-- Name: idx_messages_archived_at; Type: INDEX; Schema: public; Owner: base_user
--

CREATE INDEX idx_messages_archived_at ON public.messages USING btree (archived_at) WHERE (archived_at IS NOT NULL);


--
-- Name: idx_messages_biz_ref; Type: INDEX; Schema: public; Owner: base_user
--

CREATE INDEX idx_messages_biz_ref ON public.messages USING btree (biz_type, biz_id);


--
-- Name: idx_messages_created_at; Type: INDEX; Schema: public; Owner: base_user
--

CREATE INDEX idx_messages_created_at ON public.messages USING btree (created_at);


--
-- Name: idx_messages_event_id; Type: INDEX; Schema: public; Owner: base_user
--

CREATE INDEX idx_messages_event_id ON public.messages USING btree (event_id);


--
-- Name: idx_messages_recipient_unread; Type: INDEX; Schema: public; Owner: base_user
--

CREATE INDEX idx_messages_recipient_unread ON public.messages USING btree (recipient_type, recipient_id, is_read, created_at);


--
-- Name: messages fk_messages_notification_event; Type: FK CONSTRAINT; Schema: public; Owner: base_user
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT fk_messages_notification_event FOREIGN KEY (event_id) REFERENCES public.notification_event(id);


--
-- PostgreSQL database dump complete
--

\unrestrict vcAQgXJhTT6IUs1xohGEGbFdXDOHU1dehUt1HezR9LwZytnmVaZw5czKjOaDXJJ

